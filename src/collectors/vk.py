"""VKCollector через vk_api — читает посты со стен публичных страниц."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

from loguru import logger

from config.settings import settings
from src.collectors.base import CollectedPost, CollectorError

try:
    import vk_api
    VK_OK = True
except ImportError:  # pragma: no cover
    VK_OK = False


class VKCollector:
    def __init__(self) -> None:
        if not VK_OK:
            raise CollectorError("vk_api не установлен")
        if not settings.vk_access_token:
            raise CollectorError("VK_ACCESS_TOKEN не задан в .env")
        session = vk_api.VkApi(
            token=settings.vk_access_token,
            api_version=settings.vk_api_version,
        )
        self.api = session.get_api()

    async def fetch_new(
        self,
        domain: str,
        since_id: Optional[int] = None,
        count: int = 10,
    ) -> List[CollectedPost]:
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self.api.wall.get(domain=domain, count=count),
            )
        except Exception as e:
            logger.warning("VK wall.get '{}' failed: {}", domain, e)
            return []

        min_id = int(since_id) if since_id and str(since_id).lstrip("-").isdigit() else 0
        posts: List[CollectedPost] = []

        for item in raw.get("items", []):
            pid = int(item.get("id", 0))
            if min_id and pid <= min_id:
                continue
            text = (item.get("text") or "").strip()
            if not text or len(text) < 40:
                continue
            images: List[str] = []
            for att in item.get("attachments", []) or []:
                if att.get("type") == "photo":
                    sizes = att["photo"].get("sizes") or []
                    if sizes:
                        best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                        if best.get("url"):
                            images.append(best["url"])
            owner_id = item.get("owner_id", "")
            posts.append(
                CollectedPost(
                    source_type="vk",
                    source_id=domain,
                    external_id=str(pid),
                    text=text,
                    url=f"https://vk.com/wall{owner_id}_{pid}",
                    image_urls=images,
                    published_at=datetime.utcfromtimestamp(item["date"]) if item.get("date") else None,
                )
            )
        return posts
