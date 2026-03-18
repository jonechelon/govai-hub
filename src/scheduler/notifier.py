# src/scheduler/notifier.py
# Celo GovAI Hub — Notifier: broadcast daily digest to all subscribers (P21)

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter
from telegram.helpers import escape_markdown

from src.ai.digest_generator import digest_generator
from src.ai.groq_client import generate_proposal_summary
from src.bot.keyboards import get_digest_keyboard
from src.database.manager import DatabaseManager, db
from src.database.models import GovernanceAlert
from src.utils.health_check import set_last_digest_at
from src.utils.text_extractor import extract_proposal_text, FALLBACK_TEXT

logger = logging.getLogger(__name__)


def _format_relative_time(dt: datetime) -> str:
    """Return human-readable relative time (e.g. '2h ago')."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - dt

    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    else:
        days = seconds // 86400
        return f"{days} days ago"


def _shorten_address(address: str) -> str:
    """Shorten a 0x address to 0x1234...abcd format."""
    if len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"


def _build_governance_keyboard(tx_hash: str) -> InlineKeyboardMarkup:
    """Build InlineKeyboard for a governance alert message."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔗 CeloScan",
                    url=f"https://celoscan.io/tx/{tx_hash}",
                ),
                InlineKeyboardButton(
                    "📋 Forum",
                    url="https://forum.celo.org/c/governance",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗳️ How to Vote",
                    url="https://docs.celo.org/protocol/governance",
                ),
            ],
        ]
    )


def _build_governance_message(alert: GovernanceAlert, ai_summary: str | None = None) -> str:
    """Build the Markdown message text for a governance alert."""
    proposer_short = _shorten_address(alert.proposer)
    deposit_str = (
        f"{float(alert.deposit_cusd):.1f}" if alert.deposit_cusd is not None else "N/A"
    )
    queued_relative = _format_relative_time(alert.queued_at)
    safe_url = (
        escape_markdown(alert.description_url, version=2)
        if alert.description_url
        else None
    )
    description = safe_url or "No description provided"

    ai_block = ""
    if ai_summary:
        ai_block = (
            "\n\n💡 AI Summary\n"
            f"{ai_summary}"
        )

    return (
        "🏛️ *New Celo Governance Proposal*\n\n"
        f"📋 *Proposal \\#{alert.proposal_id}*\n"
        f"👤 Proposer: `{proposer_short}`\n"
        f"💰 Deposit: {deposit_str} cUSD\n"
        f"🕐 Queued: {queued_relative}\n\n"
        f"🔗 Description: {description}\n"
        f"{ai_block}\n\n"
        "Stay informed — vote on Celo governance matters\\! 🌿"
    )


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

    async def send_governance_alert(self, bot: Bot, alert: GovernanceAlert) -> None:
        """Broadcast a governance proposal alert to all subscribed users."""
        db_manager = DatabaseManager()
        subscribers = await db_manager.get_all_subscribers()

        if not subscribers:
            logger.info("[GOVERNANCE] No subscribers — skipping broadcast")
            return

        ai_summary_safe = ""
        try:
            if alert.description_url:
                proposal_text = await asyncio.to_thread(
                    extract_proposal_text, alert.description_url
                )
                if proposal_text and proposal_text != FALLBACK_TEXT:
                    summary = await generate_proposal_summary(proposal_text)
                    ai_summary_safe = escape_markdown(summary, version=2)
        except Exception as exc:
            logger.warning(
                "[GOVERNANCE] AI summary generation failed | id=%s | error=%s",
                alert.proposal_id,
                exc,
            )

        text = _build_governance_message(alert, ai_summary=ai_summary_safe or None)
        keyboard = _build_governance_keyboard(alert.tx_hash)

        ok_count = 0
        error_count = 0

        for user_id in subscribers:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                ok_count += 1

            except Forbidden:
                await db_manager.update_subscription(user_id, False)
                logger.info(
                    "[GOVERNANCE] User %s blocked bot — unsubscribed",
                    user_id,
                )

            except RetryAfter as e:
                logger.warning(
                    "[GOVERNANCE] RetryAfter %ss for user %s — retrying",
                    e.retry_after,
                    user_id,
                )
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                    ok_count += 1
                except Exception as retry_err:
                    logger.error(
                        "[GOVERNANCE] Retry failed for user %s: %s",
                        user_id,
                        retry_err,
                    )
                    error_count += 1

            except Exception as e:
                logger.error(
                    "[GOVERNANCE] Failed to send to user %s: %s",
                    user_id,
                    e,
                )
                error_count += 1

            await asyncio.sleep(1 / 30)

        logger.info(
            "[GOVERNANCE] Alert sent | proposal_id: %s | recipients: %s | errors: %s",
            alert.proposal_id,
            ok_count,
            error_count,
        )

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
