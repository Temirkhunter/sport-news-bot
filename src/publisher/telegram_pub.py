"""Telegram-публикатор через Bot API. Rate limiting, HTML-форматирование, один пост на новость."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

TG_CAPTION_LIMIT = 1024
TG_TEXT_LIMIT = 4096

# Telegram HTML: разрешены только b, i, u, s, code, pre, a. Markdown-обёртки запрещены.
_ALLOWED_TAGS = ("b", "i", "u", "s", "code", "pre", "a")


def _sanitize_html(text: str) -> str:
    """Удаляем запрещённые HTML-теги и markdown-обёртки, оставляем разрешённые."""
    # markdown bold/italic → удаляем символы, контент остаётся
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.*?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"_(.*?)_", r"<i>\1</i>", text, flags=re.DOTALL)

    # удаляем неподдерживаемые теги
    def _strip_tag(m: re.Match) -> str:
        tag = m.group(1).lower()
        return m.group(0) if tag in _ALLOWED_TAGS else ""

    text = re.sub(r"</?([a-zA-Z]+)[^>]*>", _strip_tag, text)
    # сжать лишние пустые строки
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_for_caption(text: str, limit: int = TG_CAPTION_LIMIT) -> str:
    """Аккуратное усечение по границе предложения, с сохранением открытых HTML-тегов."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    # обрезаем до последнего разделителя предложения
    m = re.search(r"^(.*[.!?…])\s", cut[::-1])
    if m:
        end = len(cut) - m.start()
        cut = cut[:end].rstrip()
    # закрыть незакрытые теги
    for tag in _ALLOWED_TAGS:
        opens = len(re.findall(fr"<{tag}>", cut))
        closes = len(re.findall(fr"</{tag}>", cut))
        for _ in range(opens - closes):
            cut += f"</{tag}>"
    return cut.rstrip() + "…"


class TelegramPublisher:
    def __init__(self) -> None:
        if not settings.tg_bot_token:
            raise RuntimeError("TG_BOT_TOKEN не задан")
        self.bot = Bot(token=settings.tg_bot_token)
        self._last_post_ts = 0.0

    async def _respect_rate(self) -> None:
        delta = time.time() - self._last_post_ts
        wait = settings.post_delay_seconds - delta
        if wait > 0:
            logger.debug("Rate limit: sleep {:.1f}s", wait)
            await asyncio.sleep(wait)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    async def _send_photo(self, chat: str, photo: Path, caption: str) -> None:
        try:
            with open(photo, "rb") as f:
                await self.bot.send_photo(
                    chat_id=chat,
                    photo=f,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
        except RetryAfter as e:
            logger.warning("Flood control, sleep {}s", e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    async def _send_text(self, chat: str, text: str) -> None:
        try:
            await self.bot.send_message(
                chat_id=chat,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except RetryAfter as e:
            logger.warning("Flood control, sleep {}s", e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
            raise

    async def publish(
        self,
        text: str,
        image_path: Optional[Path] = None,
        source_url: Optional[str] = None,
    ) -> bool:
        text = _sanitize_html(text)

        if settings.add_source_link and source_url:
            text = f"{text}\n\n<a href=\"{source_url}\">Источник</a>"

        chat = settings.tg_target_channel

        if settings.dry_run:
            logger.info("[DRY_RUN] Would publish ({} chars): {}", len(text), text[:160])
            return True

        await self._respect_rate()
        try:
            if image_path and Path(image_path).exists():
                # ВАЖНО: всегда ровно одно сообщение — фото + (усечённый) caption.
                caption = _truncate_for_caption(text, TG_CAPTION_LIMIT)
                await self._send_photo(chat, image_path, caption)
            else:
                await self._send_text(chat, text[:TG_TEXT_LIMIT])
            self._last_post_ts = time.time()
            return True
        except TelegramError as e:
            logger.error("Telegram publish failed: {}", e)
            return False
