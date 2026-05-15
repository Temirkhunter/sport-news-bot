"""Дедупликация постов через SHA256 хеш нормализованного текста."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from src.db import repository
from src.collectors.base import CollectedPost


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:200]


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def is_duplicate(post: CollectedPost) -> bool:
    if repository.exists_by_external(post.source_type, post.source_id, post.external_id):
        return True
    if repository.exists_by_hash(content_hash(post.text)):
        return True
    return False
