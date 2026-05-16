"""Дедупликация постов:
  - точная — по SHA256 (нормализованный текст);
  - грубая — по external_id (одно и то же сообщение из одного источника);
  - семантическая — fuzzy-match по тексту через rapidfuzz (одна история из разных источников);
  - после LLM — по `event_key` (последняя линия защиты).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional, Tuple

from loguru import logger
from rapidfuzz import fuzz

from src.collectors.base import CollectedPost
from src.db import repository

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Порог схожести (0..100). 70+ означает «почти наверняка одна и та же история».
FUZZY_THRESHOLD = 72


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:200]


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def is_duplicate(post: CollectedPost) -> bool:
    """Дешёвые проверки — до обращения к LLM."""
    if repository.exists_by_external(post.source_type, post.source_id, post.external_id):
        return True
    if repository.exists_by_hash(content_hash(post.text)):
        return True
    return False


def _fingerprint(text: str) -> str:
    """Берём первые ~500 символов нормализованного текста — для fuzzy-сравнения."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:500]


def find_similar(post_text: str, hours: int = 24, threshold: int = FUZZY_THRESHOLD) -> Optional[Tuple[int, int]]:
    """Ищет в недавних оригиналах похожий пост.
    Возвращает (db_id, score) если найден, иначе None."""
    new_fp = _fingerprint(post_text)
    if len(new_fp) < 60:
        return None  # слишком короткий текст для надёжного сравнения

    recent = repository.get_recent_originals(hours=hours, limit=200)
    best: Optional[Tuple[int, int]] = None

    for row_id, original in recent:
        old_fp = _fingerprint(original)
        if not old_fp:
            continue
        # token_set_ratio устойчив к перестановке слов и небольшим вариациям
        score = fuzz.token_set_ratio(new_fp, old_fp)
        if score >= threshold and (best is None or score > best[1]):
            best = (row_id, int(score))
            if score >= 90:  # явный дубль, дальше можно не искать
                break

    return best


def is_semantic_duplicate(post: CollectedPost, threshold: int = FUZZY_THRESHOLD) -> bool:
    """True — если в последних 24 часах уже была почти такая же история (rapidfuzz)."""
    match = find_similar(post.text, hours=24, threshold=threshold)
    if match:
        logger.info(
            "🚫 Semantic duplicate: matches DB#{} with score={} ({}/{}/{})",
            match[0], match[1], post.source_type, post.source_id, post.external_id,
        )
        return True
    return False
