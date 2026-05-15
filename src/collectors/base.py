"""Базовые типы для коллекторов."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class CollectedPost:
    """Единый формат поста, который возвращают все коллекторы."""
    source_type: str            # telegram | vk | web
    source_id: str              # имя канала/группы/фида
    external_id: str            # message_id / post_id / guid
    text: str
    url: Optional[str] = None
    image_urls: List[str] = field(default_factory=list)
    published_at: Optional[datetime] = None


class CollectorError(Exception):
    pass
