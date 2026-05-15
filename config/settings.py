"""Pydantic-конфиг приложения. Читает .env и предоставляет типизированные настройки."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
LOGS_DIR = ROOT_DIR / "logs"
DB_PATH = DATA_DIR / "posts.db"
SESSIONS_DIR = ROOT_DIR / "sessions"

for _d in (DATA_DIR, IMAGES_DIR, LOGS_DIR, SESSIONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram read (Telethon)
    tg_api_id: Optional[int] = None
    tg_api_hash: Optional[str] = None
    tg_phone: Optional[str] = None
    tg_session_name: str = "sport_bot"

    # Telegram publish (Bot API)
    tg_bot_token: str = ""
    tg_target_channel: str = ""

    # VK
    vk_access_token: Optional[str] = None
    vk_api_version: str = "5.199"

    # OpenRouter (3-уровневый каскад: primary :free → fallback :free → платный safety-net)
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-120b:free"
    openrouter_fallback_model: str = "z-ai/glm-4.5-air:free"
    openrouter_paid_fallback_model: str = "deepseek/deepseek-chat"

    # Images
    together_api_key: Optional[str] = None
    unsplash_access_key: Optional[str] = None
    pexels_api_key: Optional[str] = None

    # Schedule
    check_interval_minutes: int = 20
    post_delay_seconds: int = 30
    max_posts_per_run: int = 3

    # Только новости моложе этого возраста публикуются (часы)
    max_post_age_hours: int = 6

    # Behavior
    dry_run: bool = False
    add_source_link: bool = False
    admin_chat_id: Optional[str] = None
    log_level: str = "INFO"

    @field_validator(
        "tg_api_id",
        "tg_api_hash",
        "tg_phone",
        "vk_access_token",
        "together_api_key",
        "unsplash_access_key",
        "pexels_api_key",
        "admin_chat_id",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def session_path(self) -> Path:
        return SESSIONS_DIR / self.tg_session_name


settings = Settings()
