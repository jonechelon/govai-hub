# src/scheduler/notifier.py
# Up-to-Celo — Notifier: broadcast daily digest to all subscribers (P21)

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter

from src.bot.keyboards import get_digest_keyboard
from src.database.manager import db
from src.ai.digest_generator import digest_generator
from src.utils.health_check import set_last_digest_at

logger = logging.getLogger(__name__)


class Notifier:
    """Handles broadcasting the daily digest to all subscribers via Telegram Bot API.

    Called by DigestScheduler (P20) inside the daily job. Generates one digest,
    fetches all subscribed user IDs, sends the same digest to each with rate
    limiting and per-user error handling (Forbidden, RetryAfter), then logs
    aggregate stats to the database.
    """

    async def send_daily_digest(self, bot) -> dict:
        """Send the daily digest to all active subscribers.

        Flow: generate digest → get subscribers → send to each with rate limit
        and error handling → persist broadcast log. Never propagates exceptions
        so the scheduler job does not crash.

        Args:
            bot: The telegram.Bot instance used to send messages.

        Returns:
            Dict with keys recipients, tokens, errors (and optionally items) for
            admin_digest_now and logging. Empty dict on early failure.
        """
        # Step 1 — Generate the base digest
        try:
            result = await digest_generator.generate_digest("daily")
        except Exception as exc:
            logger.error("[NOTIFY] Digest generation failed — skipping broadcast: %s", exc, exc_info=True)
            return {}

        digest_text = result.get("text", "")
        digest_id = result.get("digest_id", "")
        sections = result.get("sections", [])
        tokens = result.get("tokens", 0)
        items_count = (
            sum(len(s.get("items", [])) for s in sections)
            if isinstance(sections, list)
            else sections
        )

        if not digest_text or not digest_id:
            logger.warning("[NOTIFY] Empty digest text or digest_id — skipping broadcast")
            return {}

        # Step 2 — Fetch subscribers
        try:
            subscriber_ids = await db.get_all_subscribers()
        except Exception as exc:
            logger.error("[NOTIFY] Failed to fetch subscribers: %s", exc, exc_info=True)
            return {}

        if not subscriber_ids:
            logger.info("[NOTIFY] No subscribers found — skipping broadcast")
            return {"recipients": 0, "tokens": tokens, "errors": 0}

        total = len(subscriber_ids)
        ok = 0
        errors = 0

        # Step 3 & 4 & 5 — Send to each user with rate limit and error handling
        for user_id in subscriber_ids:
            try:
                await db.get_user_apps_by_category(user_id)
            except Exception:
                pass
            keyboard = get_digest_keyboard(digest_id)

            sent = await self._send_to_user(bot, user_id, digest_text, keyboard)
            if sent:
                ok += 1
            else:
                errors += 1

            await asyncio.sleep(1 / 30)

        # Step 6 — Persist result and log
        try:
            await db.log_digest(
                recipients=total,
                groq_tokens=tokens,
                items=items_count,
                errors=errors,
            )
        except Exception as exc:
            logger.warning("[NOTIFY] Failed to log broadcast: %s", exc)

        # Update health check timestamp for UptimeRobot /health (P40)
        set_last_digest_at(datetime.now(timezone.utc).isoformat())

        logger.info(
            "[NOTIFY] Daily digest sent | subscribers: %d | ok: %d | errors: %d | tokens: %d",
            total,
            ok,
            errors,
            tokens,
        )

        return {"recipients": ok, "tokens": tokens, "errors": errors}

    async def _send_to_user(self, bot, user_id: int, text: str, keyboard) -> bool:
        """Send digest to one user. Handle Forbidden, RetryAfter; retry once on RetryAfter.

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return True
        except Forbidden:
            logger.info("[NOTIFY] User %s blocked the bot — unsubscribed", user_id)
            try:
                await db.update_subscription(user_id, False)
            except Exception as exc:
                logger.warning("[NOTIFY] Failed to update subscription for %s: %s", user_id, exc)
            return False
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                return True
            except Exception as retry_exc:
                logger.warning(
                    "[NOTIFY] Retry send to %s failed: %s",
                    user_id,
                    retry_exc,
                )
                return False
        except Exception as exc:
            logger.warning("[NOTIFY] Send to user %s failed: %s", user_id, exc)
            return False


# Module-level singleton used by DigestScheduler
notifier = Notifier()
