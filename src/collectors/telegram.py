"""TelegramCollector через Telethon — читает посты из каналов."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from loguru import logger

from config.settings import settings
from src.collectors.base import CollectedPost, CollectorError

try:
    from telethon import TelegramClient
    from telethon.tl.types import Message, MessageMediaPhoto
    TELETHON_OK = True
except ImportError:  # pragma: no cover
    TELETHON_OK = False


class TelegramCollector:
    def __init__(self) -> None:
        if not TELETHON_OK:
            raise CollectorError("Telethon не установлен")
        if not (settings.tg_api_id and settings.tg_api_hash):
            raise CollectorError("TG_API_ID / TG_API_HASH не заданы в .env")
        self.client = TelegramClient(
            str(settings.session_path),
            settings.tg_api_id,
            settings.tg_api_hash,
        )

    async def __aenter__(self) -> "TelegramCollector":
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise CollectorError(
                "Сессия Telegram не авторизована. Запустите `python -m src.main --init-telegram-session`"
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.disconnect()

    async def fetch_new(
        self,
        channel: str,
        since_id: Optional[int] = None,
        limit: int = 20,
    ) -> List[CollectedPost]:
        """Получает посты с message_id > since_id (или последние limit, если since_id None)."""
        posts: List[CollectedPost] = []
        try:
            entity = await self.client.get_entity(channel)
        except Exception as e:
            logger.warning("TG entity '{}' not resolved: {}", channel, e)
            return posts

        min_id = int(since_id) if since_id and str(since_id).isdigit() else 0

        async for msg in self.client.iter_messages(entity, limit=limit, min_id=min_id):
            if not isinstance(msg, Message):
                continue
            text = (msg.message or "").strip()
            if not text or len(text) < 40:
                continue
            image_urls: List[str] = []
            # Telethon download media — отложим на этап скачивания, сохраним только маркер
            if isinstance(msg.media, MessageMediaPhoto):
                image_urls.append(f"telethon://photo/{msg.id}")
            posts.append(
                CollectedPost(
                    source_type="telegram",
                    source_id=channel.lstrip("@"),
                    external_id=str(msg.id),
                    text=text,
                    url=f"https://t.me/{channel.lstrip('@')}/{msg.id}",
                    image_urls=image_urls,
                    published_at=msg.date or datetime.utcnow(),
                )
            )
        return posts


async def init_session_interactive() -> None:
    """Интерактивная авторизация Telethon (один раз)."""
    if not TELETHON_OK:
        raise CollectorError("Telethon не установлен")
    if not (settings.tg_api_id and settings.tg_api_hash and settings.tg_phone):
        raise CollectorError("Заполните TG_API_ID, TG_API_HASH, TG_PHONE в .env")

    client = TelegramClient(str(settings.session_path), settings.tg_api_id, settings.tg_api_hash)
    await client.start(phone=settings.tg_phone)
    me = await client.get_me()
    logger.info("Telegram session initialized for {}", me.username or me.id)
    await client.disconnect()
