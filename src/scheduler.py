"""APScheduler-обёртка для периодического запуска пайплайна."""
from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config.settings import settings
from src.pipeline import run_once


class AppScheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self._stop_event = asyncio.Event()
        self._cycle_lock = asyncio.Lock()

    async def _safe_run(self) -> None:
        # Защита от наложения, если предыдущий прогон ещё идёт
        if self._cycle_lock.locked():
            logger.warning("Previous cycle still running, skip this tick")
            return
        async with self._cycle_lock:
            try:
                await run_once()
            except Exception as e:
                logger.exception("Cycle crashed: {}", e)

    def start(self) -> None:
        self.scheduler.add_job(
            self._safe_run,
            trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
            id="news_cycle",
            next_run_time=None,  # первый прогон вызываем вручную
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started: every {} min", settings.check_interval_minutes)

    async def shutdown(self) -> None:
        logger.info("Scheduler shutting down (graceful)")
        # Дождаться окончания текущего цикла, потом остановить
        async with self._cycle_lock:
            self.scheduler.shutdown(wait=False)
        self._stop_event.set()

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
                except (NotImplementedError, RuntimeError):
                    # Windows: SIGTERM не поддерживается через add_signal_handler
                    pass
        except Exception:
            pass

        self.start()
        # Первый прогон сразу при старте
        await self._safe_run()
        await self._stop_event.wait()
