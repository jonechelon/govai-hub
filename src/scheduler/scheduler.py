# src/scheduler/scheduler.py
# Up-to-Celo — DigestScheduler (APScheduler AsyncIOScheduler, Europe/Madrid, P20)

from __future__ import annotations

import logging
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.utils.config_loader import CONFIG

logger = logging.getLogger(__name__)


class DigestScheduler:
    """Manages the APScheduler lifecycle for the daily digest cron job.

    Reads all scheduling parameters (timezone, time, misfire_grace_time) from
    config.yaml so nothing is hardcoded here.

    Usage:
        scheduler = DigestScheduler()
        # in post_init:  await scheduler.start(application)
        # in post_shutdown: await scheduler.shutdown()
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

        schedule_cfg = CONFIG.get("digest_schedule", {})
        tz_name: str = schedule_cfg.get("timezone", "Europe/Madrid")
        self._timezone = ZoneInfo(tz_name)
        self._timezone_name = tz_name
        self._time: str = schedule_cfg.get("time", "08:30")
        self._misfire_grace_time: int = int(schedule_cfg.get("misfire_grace_time", 120))

        self._scheduler = AsyncIOScheduler(timezone=self._timezone)
        self._initialized = True

    async def start(self, application, db) -> None:
        """Add the daily digest and payment poller jobs, then start the scheduler.

        Args:
            application: The running python-telegram-bot Application instance.
            db: DatabaseManager singleton (passed to the payment poller job).
        """
        from src.scheduler.notifier import notifier  # late import avoids circular deps
        from src.scheduler.payment_poller import run_payment_poller

        hour, minute = (int(part) for part in self._time.split(":"))

        self._scheduler.add_job(
            func=_run_daily_digest,
            trigger=CronTrigger(
                hour=hour,
                minute=minute,
                timezone=self._timezone,
            ),
            id="daily_digest",
            replace_existing=True,
            misfire_grace_time=self._misfire_grace_time,
            kwargs={"application": application, "notifier": notifier},
        )

        # Payment polling job — scans for incoming CELO transfers every 60 seconds.
        # max_instances=1 prevents overlapping runs if a scan takes longer than 60s.
        self._scheduler.add_job(
            func=run_payment_poller,
            trigger="interval",
            seconds=60,
            id="payment_poller",
            replace_existing=True,
            max_instances=1,
            kwargs={"db": db, "bot": application.bot},
        )

        if not self._scheduler.running:
            self._scheduler.start()

        job = self._scheduler.get_job("daily_digest")
        next_run = job.next_run_time if job else "unknown"
        logger.info(
            "[SCHEDULER] Daily digest scheduled at %s %s | next run: %s",
            self._time,
            self._timezone_name,
            next_run,
        )
        logger.info("[SCHEDULER] Payment poller started | interval=60s")

    async def shutdown(self) -> None:
        """Remove all jobs and shut down the scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.remove_all_jobs()
            self._scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Scheduler shut down gracefully")


async def _run_daily_digest(application, notifier) -> None:
    """Coroutine executed by APScheduler for the daily digest job.

    Args:
        application: The running Application instance (provides application.bot).
        notifier: Notifier instance that handles broadcast logic.
    """
    logger.info("[SCHEDULER] Daily digest job triggered")
    try:
        await notifier.send_daily_digest(application.bot)
    except Exception as exc:
        logger.error("[SCHEDULER] Daily digest job failed: %s", exc, exc_info=True)


# Module-level singleton used by app.py
scheduler = DigestScheduler()
