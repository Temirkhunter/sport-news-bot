"""Image provider с приоритетной цепочкой:
1) Оригинал из RSS/Telegram/VK
2) Wikipedia/Wikimedia по упомянутым командам
3) Pollinations.ai (AI-генерация)
4) Together AI (FLUX) — если есть ключ
5) Unsplash → Pexels — стоковые фото
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import quote

import httpx
from loguru import logger

from config.settings import IMAGES_DIR, settings
from src.processors.team_image import get_team_image

CATEGORY_QUERIES = {
    "football": "soccer match stadium",
    "basketball": "basketball arena game",
    "hockey": "ice hockey rink",
    "tennis": "tennis court match",
    "mma": "mma fight octagon",
    "other": "sport arena",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ImageProvider:
    def __init__(self) -> None:
        self.cleanup_old_files()

    async def get_image(
        self,
        prompt: str,
        category: str = "football",
        teams: Optional[Iterable[str]] = None,
        original_urls: Optional[Iterable[str]] = None,
        avoid_ai_portraits: bool = False,
    ) -> Optional[Path]:
        """Достаёт самое подходящее изображение, сохраняет локально, возвращает путь."""
        prompt = prompt.strip() or CATEGORY_QUERIES.get(category, "sport")
        query = CATEGORY_QUERIES.get(category, "sport")

        # 1) Оригинал из источника (BBC/Goal/Sky — обычно даёт фото матча/игрока)
        if original_urls:
            path = await self._try_original(list(original_urls))
            if path:
                logger.info("Using original source image")
                return path

        # 2) Wikipedia по команде (логотип/стадион — всегда тематично)
        if teams:
            img = await get_team_image(teams)
            if img:
                return await self._save(img, ".jpg")

        # 3) AI — Pollinations
        if not avoid_ai_portraits:
            path = await self._pollinations(prompt)
            if path:
                return path
        else:
            safe_prompt = f"{query}, action photo, ball and grass, no people faces"
            path = await self._pollinations(safe_prompt)
            if path:
                return path

        # 4) Together AI (FLUX) — если есть ключ
        if settings.together_api_key:
            path = await self._together(prompt)
            if path:
                return path

        # 5) Сток
        if settings.unsplash_access_key:
            path = await self._unsplash(query)
            if path:
                return path
        if settings.pexels_api_key:
            path = await self._pexels(query)
            if path:
                return path

        logger.warning("All image providers failed (prompt={}, category={})", prompt[:60], category)
        return None

    async def _try_original(self, urls: List[str]) -> Optional[Path]:
        """Скачиваем первое валидное изображение из списка оригиналов."""
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": DEFAULT_UA}
        ) as c:
            for url in urls:
                if not url or url.startswith("telethon://"):
                    continue
                try:
                    r = await c.get(url)
                    if r.status_code == 200 and r.content and len(r.content) > 8000:
                        ctype = r.headers.get("content-type", "").lower()
                        if "image" in ctype or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            return await self._save(r.content, _suffix_for(ctype, url))
                except Exception as e:
                    logger.debug("Original image fetch error for {}: {}", url, e)
        return None

    async def _save(self, content: bytes, suffix: str = ".jpg") -> Path:
        h = hashlib.sha256(content[:4096] + str(time.time()).encode()).hexdigest()[:24]
        path = IMAGES_DIR / f"{h}{suffix}"
        path.write_bytes(content)
        return path

    async def _pollinations(self, prompt: str) -> Optional[Path]:
        short_prompt = prompt[:300]
        urls = [
            f"https://image.pollinations.ai/prompt/{quote(short_prompt)}"
            f"?width=1024&height=1024&nologo=true&safe=true",
            f"https://image.pollinations.ai/prompt/{quote(short_prompt)}"
            f"?width=1024&height=1024&nologo=true",
        ]
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as c:
                    r = await c.get(url)
                    if r.status_code == 200 and r.content and len(r.content) > 2000:
                        return await self._save(r.content, ".jpg")
                    logger.info("Pollinations bad response: status={}, size={}", r.status_code, len(r.content))
            except Exception as e:
                logger.warning("Pollinations error: {}", e)
        return None

    async def _together(self, prompt: str) -> Optional[Path]:
        try:
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.post(
                    "https://api.together.xyz/v1/images/generations",
                    headers={"Authorization": f"Bearer {settings.together_api_key}"},
                    json={
                        "model": "black-forest-labs/FLUX.1-schnell-Free",
                        "prompt": prompt,
                        "width": 1024,
                        "height": 1024,
                        "steps": 4,
                        "n": 1,
                        "response_format": "url",
                    },
                )
                r.raise_for_status()
                img_url = r.json()["data"][0]["url"]
                img = await c.get(img_url)
                if img.status_code == 200:
                    return await self._save(img.content, ".jpg")
        except Exception as e:
            logger.warning("Together error: {}", e)
        return None

    async def _unsplash(self, query: str) -> Optional[Path]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(
                    "https://api.unsplash.com/search/photos",
                    params={"query": query, "per_page": 5, "orientation": "landscape"},
                    headers={"Authorization": f"Client-ID {settings.unsplash_access_key}"},
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                if not results:
                    return None
                photo_url = results[0]["urls"]["regular"]
                img = await c.get(photo_url)
                if img.status_code == 200:
                    return await self._save(img.content, ".jpg")
        except Exception as e:
            logger.warning("Unsplash error: {}", e)
        return None

    async def _pexels(self, query: str) -> Optional[Path]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": 5, "orientation": "landscape"},
                    headers={"Authorization": settings.pexels_api_key or ""},
                )
                r.raise_for_status()
                photos = r.json().get("photos", [])
                if not photos:
                    return None
                photo_url = photos[0]["src"]["large"]
                img = await c.get(photo_url)
                if img.status_code == 200:
                    return await self._save(img.content, ".jpg")
        except Exception as e:
            logger.warning("Pexels error: {}", e)
        return None

    def cleanup_old_files(self, days: int = 7) -> int:
        cutoff = time.time() - days * 86400
        removed = 0
        for p in IMAGES_DIR.glob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


def _suffix_for(content_type: str, url: str) -> str:
    if "png" in content_type or url.lower().endswith(".png"):
        return ".png"
    if "webp" in content_type or url.lower().endswith(".webp"):
        return ".webp"
    return ".jpg"
