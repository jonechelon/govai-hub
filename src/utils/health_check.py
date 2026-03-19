# src/utils/health_check.py
# Background health monitor: checks DB + Groq every 60 s, writes data/status.json,
# and alerts ADMIN_CHAT_ID after consecutive unhealthy cycles.
#
# The GET /health HTTP endpoint is registered in src/bot/app.py on the same
# aiohttp server that receives Telegram webhook updates. Keeping both routes
# on one server avoids port conflicts and removes the need for a separate
# health server process. _health_handler is imported directly by app.py.

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from aiohttp import web

from src.utils.env_validator import get_env_or_fail
from src.utils.paths import DATA_DIR

logger = logging.getLogger(__name__)

# Path to the status file written by HealthChecker._run_check()
STATUS_FILE = DATA_DIR / "status.json"

# Module-level state updated by set_last_digest_at() (called from Notifier)
_uptime_start = time.time()
_last_digest_at: str | None = None


def set_last_digest_at(ts: str) -> None:
    """Record the timestamp of the last successful digest broadcast."""
    global _last_digest_at
    _last_digest_at = ts


async def _health_handler(request: web.Request) -> web.Response:
    """Return a JSON health snapshot for UptimeRobot / Render health check.

    Registered by src.bot.app on the webhook aiohttp server at GET /health.
    Always returns HTTP 200 so that UptimeRobot considers the bot alive as
    long as the process is running; deeper subsystem health is in the payload.
    """
    payload = {
        "status": "ok",
        "uptime": int(time.time() - _uptime_start),
        "last_digest": _last_digest_at or "never",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return web.Response(
        text=json.dumps(payload),
        content_type="application/json",
    )


# How many consecutive unhealthy cycles before alerting admin
UNHEALTHY_THRESHOLD = 2


class HealthChecker:
    """
    Periodically checks bot subsystems and persists status to disk.
    Sends a Telegram alert to ADMIN_CHAT_ID after UNHEALTHY_THRESHOLD
    consecutive unhealthy cycles.
    """

    def __init__(self, db, bot, start_time: datetime) -> None:
        # db: DatabaseManager singleton
        # bot: telegram.Bot instance
        # start_time: datetime when the bot process started (for uptime)
        self._db = db
        self._bot = bot
        self._start_time = start_time
        self._task: asyncio.Task | None = None

        # Track consecutive unhealthy cycles for alert throttling
        self._unhealthy_streak = 0
        self._alert_sent = False  # reset when bot recovers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Register the health check loop as an asyncio background task."""
        self._task = asyncio.create_task(self._loop(), name="health_check_loop")
        logger.info("[HEALTH] Health check loop started | interval=60s")

    def stop(self) -> None:
        """Cancel the background task on shutdown."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("[HEALTH] Health check loop stopped")

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Run health checks indefinitely every 60 seconds."""
        # Wait one full cycle before the first check to let the bot settle
        await asyncio.sleep(60)

        while True:
            try:
                await self._run_check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never let the loop crash — log and continue
                logger.error(
                    "[HEALTH] Unexpected error in health loop: %s",
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(60)

    async def _run_check(self) -> None:
        """Execute one health check cycle, write status.json, alert if needed."""
        now_utc = datetime.now(timezone.utc)
        uptime_seconds = int((now_utc - self._start_time).total_seconds())

        # --- Collect metrics from DB ---
        try:
            subscribers_count = await self._db.count_subscribers()
            premium_count = await self._db.count_premium_users()
            db_healthy = True
        except Exception as exc:
            logger.warning("[HEALTH] DB check failed: %s", exc)
            subscribers_count = -1
            premium_count = -1
            db_healthy = False

        # --- Check last digest timestamp ---
        try:
            last_digest_at = await self._db.get_last_digest_at()
            last_digest_str = (
                last_digest_at.isoformat() if last_digest_at else None
            )
        except Exception:
            last_digest_str = None

        # --- Check last fetch timestamp ---
        try:
            last_fetch_at = await self._db.get_last_fetch_at()
            last_fetch_str = (
                last_fetch_at.isoformat() if last_fetch_at else None
            )
        except Exception:
            last_fetch_str = None

        # --- Check Groq reachability (lightweight — no actual generation) ---
        groq_status = await self._check_groq()

        # --- Determine overall health ---
        is_healthy = db_healthy and groq_status == "ok"
        status_str = "healthy" if is_healthy else "unhealthy"

        # Build status payload
        status = {
            "status": status_str,
            "uptime_seconds": uptime_seconds,
            "last_digest_at": last_digest_str,
            "subscribers_count": subscribers_count,
            "premium_count": premium_count,
            "last_fetch_at": last_fetch_str,
            "groq_status": groq_status,
            "checked_at": now_utc.isoformat(),
        }

        # Persist to disk
        self._write_status(status)

        logger.debug(
            "[HEALTH] %s | uptime=%ss | subscribers=%s | groq=%s",
            status_str.upper(),
            uptime_seconds,
            subscribers_count,
            groq_status,
        )

        # --- Alert logic ---
        if not is_healthy:
            self._unhealthy_streak += 1
            logger.warning(
                "[HEALTH] Unhealthy cycle %s/%s | db_ok=%s | groq=%s",
                self._unhealthy_streak,
                UNHEALTHY_THRESHOLD,
                db_healthy,
                groq_status,
            )
            if (
                self._unhealthy_streak >= UNHEALTHY_THRESHOLD
                and not self._alert_sent
            ):
                await self._send_admin_alert(status)
                self._alert_sent = True
        else:
            # Reset streak and allow future alerts when bot recovers
            if self._unhealthy_streak > 0:
                logger.info(
                    "[HEALTH] Bot recovered after %s unhealthy cycles",
                    self._unhealthy_streak,
                )
                # Notify admin of recovery if an alert was previously sent
                if self._alert_sent:
                    await self._send_admin_recovery()
            self._unhealthy_streak = 0
            self._alert_sent = False

    # ------------------------------------------------------------------
    # Subsystem checks
    # ------------------------------------------------------------------

    async def _check_groq(self) -> str:
        """
        Lightweight Groq reachability check.
        Verifies the API key is present and GroqClient is importable.
        Does NOT make an actual API call to avoid token consumption.
        """
        try:
            get_env_or_fail("GROQ_API_KEY")
            from src.ai.groq_client import GroqClient  # noqa: F401

            return "ok"
        except Exception as exc:
            logger.warning("[HEALTH] Groq check failed: %s", exc)
            return f"error: {exc}"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _write_status(self, status: dict) -> None:
        """Write status dict to data/status.json atomically."""
        try:
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file first, then rename for atomic update
            tmp = STATUS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2))
            tmp.rename(STATUS_FILE)
        except Exception as exc:
            logger.warning("[HEALTH] Failed to write status.json: %s", exc)

    # ------------------------------------------------------------------
    # Admin alerts
    # ------------------------------------------------------------------

    async def _send_admin_alert(self, status: dict) -> None:
        """Send an unhealthy alert to ADMIN_CHAT_ID."""
        try:
            admin_id = int(get_env_or_fail("ADMIN_CHAT_ID"))
            await self._bot.send_message(
                chat_id=admin_id,
                text=(
                    "Celo GovAI Hub — Health Alert\n\n"
                    f"Status: UNHEALTHY ({self._unhealthy_streak} consecutive cycles)\n\n"
                    f"DB:          {'ok' if status['subscribers_count'] >= 0 else 'ERROR'}\n"
                    f"Groq:        {status['groq_status']}\n"
                    f"Subscribers: {status['subscribers_count']}\n"
                    f"Uptime:      {status['uptime_seconds']}s\n"
                    f"Checked at:  {status['checked_at']}\n\n"
                    "Check logs immediately."
                ),
            )
            logger.warning(
                "[HEALTH] Admin alert sent | streak=%s", self._unhealthy_streak
            )
        except Exception as exc:
            logger.error("[HEALTH] Failed to send admin alert: %s", exc)

    async def _send_admin_recovery(self) -> None:
        """Notify admin that the bot has recovered from an unhealthy state."""
        try:
            admin_id = int(get_env_or_fail("ADMIN_CHAT_ID"))
            streak = self._unhealthy_streak  # capture before log says "0"
            await self._bot.send_message(
                chat_id=admin_id,
                text=(
                    "Celo GovAI Hub — Recovered\n\n"
                    "Status: HEALTHY\n\n"
                    f"Bot recovered after {streak} unhealthy cycles.\n"
                    "All systems are operational."
                ),
            )
            logger.info("[HEALTH] Admin recovery notification sent")
        except Exception as exc:
            logger.error(
                "[HEALTH] Failed to send recovery notification: %s", exc
            )
