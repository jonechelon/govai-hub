# src/scheduler/ai_trade_reminders.py
# One-off reminders after the AI Trade result screen (option B: clock starts when UX is complete).

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.date import DateTrigger
from telegram import Bot
from telegram.constants import ParseMode

from src.scheduler.scheduler import scheduler as digest_scheduler

logger = logging.getLogger(__name__)


async def _send_ai_trade_reminder(bot: Bot, chat_id: int, phase: str) -> None:
    """Send a follow-up nudge for users who completed the AI Trade flow."""
    if phase == "1h":
        text = (
            "⏰ <b>AI Trade reminder</b>\n\n"
            "You opened on-chain shortcuts earlier. Tap <b>💹 AI Trade</b> in the menu "
            "when you're ready to revisit Daily Sources and shortcuts."
        )
    else:
        text = (
            "⏰ <b>AI Trade reminder</b>\n\n"
            "It's been about a day since you used AI Trade. Open <b>💹 AI Trade</b> "
            "from the menu for fresh suggestions."
        )
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info(
            "[AI_TRADE_REMINDER] Sent %s reminder | chat_id=%s",
            phase,
            chat_id,
        )
    except Exception as exc:
        logger.warning(
            "[AI_TRADE_REMINDER] Failed to send %s reminder | chat_id=%s | %s",
            phase,
            chat_id,
            exc,
        )


def _cancel_pending_ai_trade_reminders_for_chat(sched, chat_id: int) -> None:
    """Remove any not-yet-fired AI Trade reminder jobs for this chat (latest session wins)."""
    prefix = f"ai_trade_rem_{chat_id}_"
    for job in list(sched.get_jobs()):
        jid = job.id or ""
        if jid.startswith(prefix):
            try:
                sched.remove_job(jid)
            except Exception:
                pass


def schedule_ai_trade_reminders_after_screen(bot: Bot, chat_id: int) -> None:
    """Schedule 1h and 24h reminders after the AI Trade keyboard is shown (option B)."""
    sched = digest_scheduler.scheduler
    if not sched.running:
        logger.debug("[AI_TRADE_REMINDER] Scheduler not running — skip")
        return

    _cancel_pending_ai_trade_reminders_for_chat(sched, chat_id)

    uid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)

    for delay_hours, phase in ((1, "1h"), (24, "24h")):
        run_at = now + timedelta(hours=delay_hours)
        job_id = f"ai_trade_rem_{chat_id}_{uid}_{phase}"
        sched.add_job(
            _send_ai_trade_reminder,
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            replace_existing=False,
            kwargs={"bot": bot, "chat_id": chat_id, "phase": phase},
            misfire_grace_time=3600,
        )

    logger.info(
        "[AI_TRADE_REMINDER] Scheduled 1h and 24h | chat_id=%s | session=%s",
        chat_id,
        uid,
    )
