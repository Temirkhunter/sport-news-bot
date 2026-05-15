"""Репозиторий доступа к ProcessedPost."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, select

from src.db.models import ProcessedPost, get_session


def exists_by_hash(content_hash: str) -> bool:
    with get_session() as s:
        stmt = select(ProcessedPost.id).where(ProcessedPost.content_hash == content_hash)
        return s.execute(stmt).first() is not None


def exists_by_external(source_type: str, source_id: str, external_id: str) -> bool:
    with get_session() as s:
        stmt = select(ProcessedPost.id).where(
            and_(
                ProcessedPost.source_type == source_type,
                ProcessedPost.source_id == source_id,
                ProcessedPost.external_id == external_id,
            )
        )
        return s.execute(stmt).first() is not None


def get_last_external_id(source_type: str, source_id: str) -> Optional[str]:
    """Возвращает максимальный external_id для источника (для инкрементального опроса)."""
    with get_session() as s:
        stmt = (
            select(ProcessedPost.external_id)
            .where(
                and_(
                    ProcessedPost.source_type == source_type,
                    ProcessedPost.source_id == source_id,
                )
            )
            .order_by(ProcessedPost.created_at.desc())
            .limit(1)
        )
        row = s.execute(stmt).first()
        return row[0] if row else None


def create_pending(
    *,
    source_type: str,
    source_id: str,
    external_id: str,
    content_hash: str,
    original_text: str,
) -> int:
    with get_session() as s:
        post = ProcessedPost(
            source_type=source_type,
            source_id=source_id,
            external_id=external_id,
            content_hash=content_hash,
            original_text=original_text,
            status="pending",
            created_at=datetime.utcnow(),
        )
        s.add(post)
        s.commit()
        return post.id


def mark_published(post_id: int, rewritten_text: str, image_path: Optional[str]) -> None:
    with get_session() as s:
        post = s.get(ProcessedPost, post_id)
        if not post:
            return
        post.rewritten_text = rewritten_text
        post.image_path = image_path
        post.status = "published"
        post.published_at = datetime.utcnow()
        s.commit()


def mark_failed(post_id: int, reason: str = "") -> None:
    with get_session() as s:
        post = s.get(ProcessedPost, post_id)
        if not post:
            return
        post.status = "failed"
        if reason:
            post.rewritten_text = (post.rewritten_text or "") + f"\n[FAIL]{reason}"
        s.commit()


def cleanup_old(days: int = 30) -> int:
    """Удаляет записи старше N дней. Возвращает количество удалённых."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    with get_session() as s:
        old = s.query(ProcessedPost).filter(ProcessedPost.created_at < cutoff).all()
        n = len(old)
        for p in old:
            s.delete(p)
        s.commit()
        return n
