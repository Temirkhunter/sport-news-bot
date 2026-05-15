"""Точка входа: CLI с режимами --once / --init-telegram-session / по умолчанию scheduler."""
from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from config.settings import LOGS_DIR, settings
from src.db.models import init_db


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, enqueue=False)
    logger.add(
        LOGS_DIR / "bot_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sport-news-bot", description="Sport News Telegram bot")
    p.add_argument("--once", action="store_true", help="Один прогон пайплайна без планировщика")
    p.add_argument(
        "--init-telegram-session",
        action="store_true",
        help="Интерактивная авторизация Telethon (один раз)",
    )
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    setup_logging()
    init_db()
    logger.info("Sport News Bot starting. dry_run={}", settings.dry_run)

    if args.init_telegram_session:
        from src.collectors.telegram import init_session_interactive
        await init_session_interactive()
        return 0

    if args.once:
        from src.pipeline import run_once
        published = await run_once()
        logger.info("Done. Published {}", published)
        return 0

    from src.scheduler import AppScheduler
    sched = AppScheduler()
    await sched.run_forever()
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
