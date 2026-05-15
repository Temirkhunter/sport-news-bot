"""WebCollector через feedparser — RSS-фиды футбольных новостей."""
from __future__ import annotations

import asyncio
import re as _re
from datetime import datetime
from time import mktime
from typing import Iterable, List, Optional

import feedparser
import httpx
from loguru import logger

from src.collectors.base import CollectedPost

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class WebCollector:
    async def fetch(
        self,
        feed_url: str,
        source_name: str,
        keywords: Optional[Iterable[str]] = None,
        limit: int = 15,
    ) -> List[CollectedPost]:
        # Сначала скачиваем фид с настоящим User-Agent, иначе часть СМИ отдаёт HTML-блок 403/cf
        raw: Optional[bytes] = None
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(
                    feed_url,
                    headers={"User-Agent": DEFAULT_UA, "Accept": "application/rss+xml, application/xml, */*"},
                )
                if r.status_code == 200 and r.content:
                    raw = r.content
        except Exception as e:
            logger.warning("RSS fetch error for {}: {}", feed_url, e)

        if raw is None:
            return []

        loop = asyncio.get_running_loop()
        try:
            parsed = await loop.run_in_executor(None, feedparser.parse, raw)
        except Exception as e:
            logger.warning("RSS parse error for {}: {}", feed_url, e)
            return []

        if parsed.bozo and not parsed.entries:
            logger.warning("RSS '{}' returned no entries (bozo={})", source_name, parsed.bozo_exception)
            return []

        kw = [k.lower() for k in (keywords or [])]
        posts: List[CollectedPost] = []

        for entry in parsed.entries[:limit]:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            summary = _strip_html(summary)
            full = f"{title}\n\n{summary}".strip()
            if not full or len(full) < 60:
                continue
            if kw and not any(k in full.lower() for k in kw):
                continue

            ext_id = entry.get("id") or entry.get("link") or title[:80]
            published_at: Optional[datetime] = None
            if getattr(entry, "published_parsed", None):
                published_at = datetime.fromtimestamp(mktime(entry.published_parsed))

            images: List[str] = []
            # media:thumbnail
            for t in entry.get("media_thumbnail", []) or []:
                if t.get("url"):
                    images.append(t["url"])
            # media:content
            for media in entry.get("media_content", []) or []:
                if media.get("url"):
                    images.append(media["url"])
            # enclosure
            for link in entry.get("links", []) or []:
                if link.get("type", "").startswith("image/") and link.get("href"):
                    images.append(link["href"])
            # rss:image
            if entry.get("image", {}).get("href"):
                images.append(entry["image"]["href"])
            # og:image из summary как fallback
            if not images and summary:
                m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.get("summary", "") or "")
                if m:
                    images.append(m.group(1))

            posts.append(
                CollectedPost(
                    source_type="web",
                    source_id=source_name,
                    external_id=str(ext_id),
                    text=full,
                    url=entry.get("link"),
                    image_urls=images,
                    published_at=published_at,
                )
            )
        return posts


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html
