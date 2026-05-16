"""Главный пайплайн обработки: collect → dedup → rewrite → image → publish."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import yaml
from loguru import logger

from config.settings import ROOT_DIR, settings
from src.collectors.base import CollectedPost
from src.collectors.web import WebCollector
from src.db import repository
from src.processors import deduplicator, usage_tracker
from src.processors.image_gen import ImageProvider
from src.processors.rewriter import RewriterError, rewrite
from src.publisher.telegram_pub import TelegramPublisher

# Простая эвристика для определения упоминания реальных людей —
# отключает AI-генерацию портретов в пользу стоковых фото.
NAME_HINT_RE = re.compile(r"\b[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2}\b")


def _load_sources() -> dict:
    path = ROOT_DIR / "config" / "sources.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def collect_all() -> List[CollectedPost]:
    """Запускает все включённые коллекторы параллельно."""
    cfg = _load_sources()
    tasks: List[asyncio.Task] = []

    # --- Web (RSS) ---
    web = WebCollector()
    for src in cfg.get("web", []) or []:
        if not src.get("enabled"):
            continue
        tasks.append(asyncio.create_task(
            web.fetch(src["url"], src["name"], src.get("keywords"))
        ))

    # --- VK ---
    if settings.vk_access_token:
        try:
            from src.collectors.vk import VKCollector
            vk = VKCollector()
            for src in cfg.get("vk", []) or []:
                if not src.get("enabled"):
                    continue
                tasks.append(asyncio.create_task(vk.fetch_new(src["domain"])))
        except Exception as e:
            logger.warning("VK collector skipped: {}", e)
    else:
        logger.info("VK_ACCESS_TOKEN не задан — VK-коллектор пропущен")

    # --- Telegram ---
    tg_enabled_count = sum(1 for s in (cfg.get("telegram") or []) if s.get("enabled"))
    if tg_enabled_count and settings.tg_api_id and settings.tg_api_hash:
        try:
            from src.collectors.telegram import TelegramCollector

            async def _tg_run() -> List[CollectedPost]:
                out: List[CollectedPost] = []
                async with TelegramCollector() as tg:
                    for src in cfg.get("telegram", []) or []:
                        if not src.get("enabled"):
                            continue
                        try:
                            posts = await tg.fetch_new(src["channel"])
                            out.extend(posts)
                        except Exception as e:
                            logger.warning("TG fetch failed for {}: {}", src["channel"], e)
                return out

            tasks.append(asyncio.create_task(_tg_run()))
        except Exception as e:
            logger.warning("Telegram collector skipped: {}", e)
    else:
        logger.debug("Telegram-коллектор пропущен (нет включённых TG-источников или нет креденшелов)")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    posts: List[CollectedPost] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Collector failed: {}", r)
            continue
        posts.extend(r)
    logger.info("Collected {} raw posts from {} collectors", len(posts), len(tasks))
    return posts


async def process_post(
    post: CollectedPost,
    image_provider: ImageProvider,
    publisher: TelegramPublisher,
) -> bool:
    """Полный путь одного поста. Возвращает True если опубликован."""
    # 1) Точный дубль (external_id или SHA256 нормализованного текста)
    if deduplicator.is_duplicate(post):
        logger.debug("Duplicate skipped: {}/{}/{}", post.source_type, post.source_id, post.external_id)
        return False

    # 2) Семантический дубль (та же новость другими словами из другого источника) — до LLM
    if deduplicator.is_semantic_duplicate(post):
        return False

    chash = deduplicator.content_hash(post.text)
    try:
        post_id = repository.create_pending(
            source_type=post.source_type,
            source_id=post.source_id,
            external_id=post.external_id,
            content_hash=chash,
            original_text=post.text,
        )
    except Exception as e:
        logger.warning("DB insert failed (likely race dup): {}", e)
        return False

    logger.info("→ rewrite: {}/{}", post.source_type, post.source_id)
    try:
        result = await rewrite(post.text, post.source_id)
    except RewriterError as e:
        logger.error("Rewriter failed: {}", e)
        repository.mark_failed(post_id, str(e))
        return False
    except Exception as e:
        logger.error("Rewriter unexpected error: {}", e)
        repository.mark_failed(post_id, str(e))
        return False

    # 3) Семантический дубль по event_key — после LLM, последняя линия защиты
    if result.event_key and repository.exists_event_key(result.event_key, hours=24):
        logger.info("🚫 Dedup by event_key: '{}' already seen in last 24h", result.event_key)
        repository.mark_failed(post_id, f"dup_event_key:{result.event_key}")
        return False
    if result.event_key:
        repository.set_event_key(post_id, result.event_key)

    # Эвристика: если в посте упоминаются имена людей — избегаем AI-портретов
    avoid_portraits = bool(NAME_HINT_RE.search(result.text))

    logger.info(
        "→ image: category={}, teams={}, originals={}, avoid_portraits={}",
        result.category, result.teams, len(post.image_urls), avoid_portraits,
    )
    img_path = await image_provider.get_image(
        prompt=result.image_prompt,
        category=result.category,
        teams=result.teams,
        original_urls=post.image_urls,
        avoid_ai_portraits=avoid_portraits,
    )

    logger.info("→ publish: img={}", bool(img_path))
    ok = await publisher.publish(result.text, img_path, post.url)
    if ok:
        repository.mark_published(post_id, result.text, str(img_path) if img_path else None)
        logger.success("✓ published: {}/{}/{}", post.source_type, post.source_id, post.external_id)
    else:
        repository.mark_failed(post_id, "publish failed")
    return ok


def _to_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def run_once() -> int:
    """Один полный цикл. Возвращает число опубликованных постов."""
    posts = await collect_all()

    # Фильтр свежести: оставляем только новости моложе MAX_POST_AGE_HOURS
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.max_post_age_hours)
    fresh: List[CollectedPost] = []
    for p in posts:
        ts = _to_utc(p.published_at)
        if ts is None:
            # без даты — считаем «недавним», публикуем
            fresh.append(p)
        elif ts >= cutoff:
            fresh.append(p)
    skipped = len(posts) - len(fresh)
    if skipped:
        logger.info("Filtered out {} stale posts (older than {}h)", skipped, settings.max_post_age_hours)

    # Сортируем по дате публикации (свежее → раньше); посты без даты в конец
    fresh.sort(key=lambda p: _to_utc(p.published_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    posts = fresh

    image_provider = ImageProvider()
    publisher = TelegramPublisher()

    published = 0
    for post in posts:
        if published >= settings.max_posts_per_run:
            logger.info("Reached MAX_POSTS_PER_RUN={}", settings.max_posts_per_run)
            break
        if await process_post(post, image_provider, publisher):
            published += 1

    # Чистим старые записи в БД и старые изображения
    try:
        removed = repository.cleanup_old(days=30)
        if removed:
            logger.info("Cleaned {} old DB rows", removed)
        image_provider.cleanup_old_files(days=7)
    except Exception as e:
        logger.warning("Cleanup failed: {}", e)

    logger.info("Cycle finished: published={}/{}", published, len(posts))
    usage_tracker.log_summary()
    return published
