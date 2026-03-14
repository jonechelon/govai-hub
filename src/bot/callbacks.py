# src/bot/callbacks.py
# Up-to-Celo — central callback router for inline keyboards (P8)

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

from src.bot.handlers import HELP_MESSAGE, PREMIUM_MESSAGE
from src.bot.keyboards import (
    get_details_keyboard,
    get_digest_keyboard,
    get_main_keyboard,
    get_premium_keyboard,
    get_settings_keyboard,
)
from src.database.manager import db
from src.utils.env_validator import get_env_or_fail

logger = logging.getLogger(__name__)

DIGEST_CACHE_DIR = Path("data/cache")

# Regex to extract URLs from digest text (limit to http(s) links)
_URL_RE = re.compile(r"https?://[^\s\)\]\>\"]+")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks. MUST call query.answer() first (2.E1)."""
    query = update.callback_query
    await query.answer()  # MUST be first — prevents infinite loading spinner

    data: str = query.data or ""
    user_id: int = update.effective_user.id

    if data == "noop":
        return  # silently ignore category header buttons
    elif data == "digest:latest":
        await _handle_digest_latest(query, context, user_id)
    elif data.startswith("details:"):
        _, digest_id = data.split(":", 1)
        await _handle_details(query, context, digest_id)
    elif data.startswith("links:"):
        _, digest_id = data.split(":", 1)
        await _handle_links(query, context, digest_id)
    elif data.startswith("ask:"):
        _, digest_id = data.split(":", 1)
        await _handle_ask(query, context, user_id, digest_id)
    elif data == "settings:open":
        await _handle_settings_open(query, context, user_id)
    elif data == "settings_close":
        await _handle_settings_close(query)
    elif data.startswith("toggle_app:"):
        await _handle_toggle_app(query, user_id)
    elif data == "premium:open":
        await _handle_premium_open(query, context, user_id)
    elif data in ("premium:7d", "premium:30d"):
        await _handle_premium_info(query, data)
    elif data == "premium:confirm":
        await _handle_premium_confirm(query)
    elif data.startswith("back:"):
        await _handle_back(query, user_id)
    elif data == "help:open":
        await _handle_help_open(query)
    else:
        logger.warning("[CALLBACK] Unknown callback_data: %s from user %d", data, user_id)


# ── digest ────────────────────────────────────────────────────────────────────

async def _handle_digest_latest(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Stub until P22 — prompt user to use /digest."""
    await query.answer(
        "📰 Use /digest to get today's digest.",
        show_alert=True,
    )


async def _handle_details(query, context: ContextTypes.DEFAULT_TYPE, digest_id: str) -> None:
    """Expand the digest message to full view with the details keyboard."""
    cache_file = DIGEST_CACHE_DIR / f"digest_{digest_id}.json"
    if not cache_file.exists():
        await query.answer("⚠️ Digest not found. It may have expired.", show_alert=True)
        return
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[DETAILS] Failed to load digest %s | error: %s", digest_id, exc)
        await query.answer("❌ Failed to load digest.", show_alert=True)
        return
    text = payload.get("text", "")
    if not text:
        await query.answer("⚠️ Digest content is empty.", show_alert=True)
        return
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_details_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass
    logger.info("[DETAILS] Digest %s loaded for user in details view", digest_id)


async def _handle_links(query, context: ContextTypes.DEFAULT_TYPE, digest_id: str) -> None:
    """List up to 15 unique URLs from the digest cache."""
    cache_file = DIGEST_CACHE_DIR / f"digest_{digest_id}.json"
    if not cache_file.exists():
        await query.answer("⚠️ Digest not found.", show_alert=True)
        return
    try:
        raw = cache_file.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[CALLBACK] Failed to read digest cache %s: %s", digest_id, e)
        await query.answer("⚠️ Digest not found.", show_alert=True)
        return
    text = payload.get("text", "")
    urls = list(dict.fromkeys(_URL_RE.findall(text)))[:15]
    if not urls:
        await query.message.reply_text("🔗 No links found in this digest.")
        return
    lines = [f"{i}. {u}" for i, u in enumerate(urls, 1)]
    body = "\n".join(lines)
    header = f"🔗 *Links — Digest {digest_id}*\n\n"
    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Back to digest", callback_data=f"details:{digest_id}")]
    ])
    await query.message.reply_text(
        header + body,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard,
    )


async def _handle_back(query, user_id: int) -> None:
    """Return to digest original view with the standard digest keyboard."""
    digest_id = query.data.split(":", 1)[1]
    cache_file = DIGEST_CACHE_DIR / f"digest_{digest_id}.json"
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        text = payload.get("text", "")
    except Exception:
        await query.answer("❌ Could not reload digest.", show_alert=True)
        return
    if not text:
        await query.answer("❌ Could not reload digest.", show_alert=True)
        return
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_digest_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass
    logger.info("[DETAILS] Back to digest view | digest=%s | user=%d", digest_id, user_id)


async def _handle_ask(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int, digest_id: str
) -> None:
    """Activate ask mode with digest context (AI logic in P26/P27)."""
    context.user_data["ask_digest_id"] = digest_id
    context.user_data["ask_active"] = True
    msg = (
        "🤖 *Ask AI — Celo Ecosystem*\n\n"
        "Ask anything about the Celo ecosystem.\n"
        "I'll use this digest as context.\n\n"
        "Type your question now, or use /ask <question>\n"
        "Type /stop to end this session."
    )
    await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ── settings ───────────────────────────────────────────────────────────────────

async def _handle_settings_open(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Open settings menu with app toggles reflecting live DB state."""
    user_apps = await db.get_user_apps_by_category(user_id)
    text = "⚙️ <b>Up-to-Celo — Select Your Apps</b>\n\nTap an app to toggle it on/off."
    try:
        await query.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_settings_keyboard(user_apps),
        )
    except BadRequest:
        pass


async def _handle_settings_close(query) -> None:
    """Close settings and show save confirmation."""
    try:
        await query.edit_message_text(
            text="✅ <b>Settings saved.</b>\nYour digest will reflect your app selection.",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass


async def _handle_toggle_app(query, user_id: int) -> None:
    """Toggle one app and refresh the settings keyboard with updated DB state.

    app_name is read directly from query.data as lowercase — no display-name conversion needed.
    """
    app_name: str = query.data.split(":", 1)[1]

    user_apps = await db.get_user_apps_by_category(user_id)
    enabled_apps = [a for apps in user_apps.values() for a in apps]
    currently_enabled = app_name in enabled_apps

    # Guard: never disable the last enabled app
    if currently_enabled and await db.count_enabled_apps(user_id) <= 1:
        await query.answer("⚠️ Select at least one app.", show_alert=True)
        return

    await db.update_user_app(user_id, app_name, enabled=not currently_enabled)

    updated_apps = await db.get_user_apps_by_category(user_id)
    try:
        await query.edit_message_reply_markup(
            reply_markup=get_settings_keyboard(updated_apps),
        )
    except BadRequest:
        pass

    logger.info(
        "[SETTINGS] user=%d | toggle app=%s | enabled=%s",
        user_id,
        app_name,
        not currently_enabled,
    )


# ── premium ────────────────────────────────────────────────────────────────────

async def _handle_premium_open(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Show premium message (already premium or free with wallet)."""
    is_premium = await db.is_premium(user_id)
    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")
    if is_premium:
        text = (
            "⭐ *You're already Premium!*\n\n"
            "Enjoy unlimited AI asks with llama-3.3-70b-versatile.\n"
            "Use /status to check your expiration date."
        )
    else:
        text = PREMIUM_MESSAGE.format(BOT_WALLET=bot_wallet)
    try:
        await query.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_premium_keyboard(),
        )
    except BadRequest:
        pass


async def _handle_premium_info(query, data: str) -> None:
    """Show short premium plan info in alert."""
    if data == "premium:7d":
        msg = (
            "⭐ 7-day Premium: send 0.50 cUSD to the bot wallet, then tap \"I sent\"."
        )
    else:
        msg = (
            "⭐ 30-day Premium: send 1.50 cUSD to the bot wallet, then tap \"I sent\"."
        )
    await query.answer(msg, show_alert=True)


async def _handle_premium_confirm(query) -> None:
    """Redirect user to /confirmpayment command."""
    await query.message.reply_text(
        "✅ *Confirm your payment*\n\n"
        "Send the transaction hash using:\n"
        "`/confirmpayment <tx_hash>`\n\n"
        "Example:\n`/confirmpayment 0xabc123...`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── help ───────────────────────────────────────────────────────────────────────

async def _handle_help_open(query) -> None:
    """Show help message inline."""
    await query.message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.MARKDOWN)


# ── export for app.py ──────────────────────────────────────────────────────────

callback_query_handler = CallbackQueryHandler(callback_router)
