"""SQLAlchemy-модели и инициализация SQLite-БД."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config.settings import DB_PATH


class Base(DeclarativeBase):
    pass


class ProcessedPost(Base):
    __tablename__ = "processed_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(32), nullable=False)  # telegram | vk | web
    source_id = Column(String(255), nullable=False)
    external_id = Column(String(255), nullable=False)
    content_hash = Column(String(64), unique=True, nullable=False)
    original_text = Column(Text)
    rewritten_text = Column(Text)
    image_path = Column(String(512))
    status = Column(String(32), default="pending")  # pending | published | failed
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime)

    __table_args__ = (
        Index("idx_hash", "content_hash"),
        Index("idx_source", "source_type", "source_id", "external_id"),
    )


_engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)


def init_db() -> None:
    Base.metadata.create_all(_engine)


def get_session() -> Session:
    return SessionLocal()
