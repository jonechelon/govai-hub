# src/scheduler/scheduler.py
# Up-to-Celo — DigestScheduler singleton (APScheduler, BRT)
# Minimal implementation for P5 lifecycle; P20 adds daily digest cron job.

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


class DigestScheduler:
    """Singleton that holds the APScheduler instance.

    start() starts the scheduler (P20 will add the 08:30 BRT daily job).
    shutdown() stops it gracefully.
    """

    _instance: Optional[DigestScheduler] = None

    def __new__(cls) -> DigestScheduler:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._scheduler = AsyncIOScheduler(timezone="America/Fortaleza")
        self._initialized = True

    def start(self) -> None:
        """Start the scheduler. P20 will add the daily digest job here."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("[SCHEDULER] Scheduler started (daily job will be added in P20)")

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("[SCHEDULER] Scheduler shut down")


# Singleton instance for app lifecycle (post_init / post_shutdown)
scheduler = DigestScheduler()
