from __future__ import annotations

import glob
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import uuid

import telegram.error

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, InlineQueryHandler, ContextTypes
from telegram.helpers import escape_markdown

from src.ai.digest_generator import digest_generator
from src.ai.groq_client import groq_client
from src.bot.keyboards import get_digest_keyboard, get_main_keyboard, get_premium_keyboard, get_settings_keyboard
from src.database.manager import db
from src.fetchers.fetcher_manager import fetcher_manager
from src.fetchers.payment_fetcher import PaymentFetcher  # P-onchain — not yet implemented
from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail
from src.utils.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

WELCOME_MESSAGE = (
    "👋 Welcome to *Up-to-Celo*!\n\n"
    "Be up-to-date on the Celo Blockchain — daily AI digests delivered at 08:30 (Europe/Madrid).\n\n"
    "What you get:\n"
    "-  📰 Daily digest with news, DeFi, ReFi, payments & on-chain data\n"
    "-  🤖 Ask AI about the Celo ecosystem\n"
    "-  ⚙️ Customize which apps you follow\n\n"
    "Use the menu below or type /help to see all commands."
)

HELP_MESSAGE = (
    "📖 *Up-to-Celo — Commands*\n\n"
    "/start — Start the bot & subscribe to daily digests\n"
    "/digest — Get today's digest now (60 min cooldown)\n"
    "/status — Check your plan and next digest time\n"
    "/settings — Choose which apps to follow\n"
    "/premium — Upgrade to Premium (unlimited AI, better model)\n"
    "/confirmpayment <tx_hash> — Confirm your cUSD payment\n"
    "/ask <question> — Ask AI about Celo ecosystem (conversational)\n"
    "/stop — End current AI conversation\n"
    "/subscribe — Re-enable daily digests\n"
    "/unsubscribe — Stop receiving daily digests\n"
    "/help — Show this message"
)

PREMIUM_MESSAGE = (
    "⭐ *Upgrade to Up-to-Celo Premium*\n\n"
    "*Current plan:* 🆓 Free (3 asks/day · llama-3.1-8b)\n\n"
    "*Send cUSD to:*\n"
    "`{BOT_WALLET}`\n\n"
    "💰 0.50 cUSD → 7 days Premium\n"
    "💰 1.50 cUSD → 30 days Premium\n\n"
    "ℹ️ Send from a personal wallet (MiniPay, Valora, MetaMask).\n"
    "Exchanges use intermediate addresses and won't be detected.\n\n"
    "After sending, tap the button below or use:\n"
    "/confirmpayment <tx_hash>"
)


# ── /start ─────────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    user_id = user.id
    username = user.username
    first_name = user.first_name

    await db.get_or_create_user(user_id, username, first_name)
    await db.update_subscription(user_id, True)

    await update.message.reply_text(
        WELCOME_MESSAGE,
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        HELP_MESSAGE,
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /subscribe / /unsubscribe ──────────────────────────────────────────────────

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /subscribe — enable daily digest for the user."""
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username, user.first_name)
    await db.update_subscription(user.id, subscribed=True)
    logger.info("[BOT] /subscribe | user: %d", user.id)
    await update.message.reply_text(
        "✅ You're subscribed! You'll receive daily Celo digests at 08:30 (Europe/Madrid).",
        parse_mode=ParseMode.MARKDOWN,
    )


async def unsubscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unsubscribe — disable daily digest for the user."""
    user = update.effective_user
    await db.update_subscription(user.id, subscribed=False)
    logger.info("[BOT] /unsubscribe | user: %d", user.id)
    await update.message.reply_text(
        "🔕 You've unsubscribed from daily digests. "
        "Use /subscribe to re-enable anytime.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /status ────────────────────────────────────────────────────────────────────

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show the user's plan and next digest time."""
    user = update.effective_user
    user_id = user.id
    username = user.username
    first_name = user.first_name

    is_premium = await db.is_premium(user_id)
    user_record = await db.get_or_create_user(user_id, username, first_name)
    premium_expires_at = getattr(user_record, "premium_expires_at", None)

    if is_premium and premium_expires_at:
        message = (
            "📊 *Your Up-to-Celo Status*\n\n"
            "Plan: ⭐ Premium\n"
            f"Expires: {premium_expires_at.strftime('%b %d, %Y')}\n"
            "AI model: llama-3.3-70b-versatile (unlimited asks)\n\n"
            "🕐 Next digest: today at 08:30 (Europe/Madrid)"
        )
    else:
        message = (
            "📊 *Your Up-to-Celo Status*\n\n"
            "Plan: 🆓 Free\n"
            "AI model: llama-3.1-8b-instant (3 asks/day)\n\n"
            "🕐 Next digest: today at 08:30 (Europe/Madrid)\n\n"
            "Upgrade with /premium to unlock unlimited AI."
        )

    await update.message.reply_text(
        message,
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /premium ───────────────────────────────────────────────────────────────────

async def premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /premium — show or upgrade to Premium."""
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name

    is_premium = await db.is_premium(user_id)
    user_record = await db.get_or_create_user(user_id, username, first_name)
    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")

    if is_premium and getattr(user_record, "premium_expires_at", None):
        message = (
            "⭐ *You're already Premium!*\n\n"
            "Enjoy unlimited AI asks with llama-3.3-70b-versatile.\n"
            "Use /status to check your expiration date."
        )
    else:
        message = PREMIUM_MESSAGE.format(BOT_WALLET=bot_wallet)

    await update.message.reply_text(
        message,
        reply_markup=get_premium_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /confirmpayment ────────────────────────────────────────────────────────────

async def confirm_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /confirmpayment <tx_hash> — verify cUSD transfer and activate Premium."""
    user = update.effective_user
    user_id = user.id
    username = user.username
    first_name = user.first_name

    args = context.args or []

    if not args:
        await update.message.reply_text(
            "⚠️ Please provide a transaction hash.\n"
            "Usage: /confirmpayment <tx_hash>",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    tx_hash = args[0].strip()

    if not _TX_HASH_RE.match(tx_hash):
        await update.message.reply_text(
            "❌ Invalid transaction hash format.\n"
            "Expected: 0x followed by 64 hex characters.\n"
            "Example: /confirmpayment 0xabc123...",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        "🔍 Verifying your payment on-chain...",
        parse_mode=ParseMode.MARKDOWN,
    )

    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")
    result = await PaymentFetcher().verify_tx(tx_hash, bot_wallet)

    if result is None:
        await update.message.reply_text(
            "❌ *Payment not found or invalid.*\n\n"
            "Possible reasons:\n"
            f"• Transaction not confirmed yet (wait ~30s and try again)\n"
            f"• Sent to wrong address — expected: `{bot_wallet}`\n"
            "• Amount below minimum (0.50 cUSD for 7 days)\n"
            "• Sent from an exchange (use MiniPay, Valora or MetaMask)\n\n"
            f"Transaction hash received: `{tx_hash}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    value_cusd: float = result["value_cusd"]
    payment_cfg = CONFIG["payment"]
    min_monthly: float = payment_cfg["min_cusd_monthly"]
    min_weekly: float = payment_cfg["min_cusd_weekly"]

    if value_cusd >= min_monthly:
        days = payment_cfg["days_monthly"]
    elif value_cusd >= min_weekly:
        days = payment_cfg["days_weekly"]
    else:
        await update.message.reply_text(
            "❌ Payment amount below minimum required for Premium.\n\n"
            f"Received: {value_cusd:.2f} cUSD\n"
            f"Minimum for 7-day Premium: {min_weekly:.2f} cUSD",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await db.get_or_create_user(user_id, username, first_name)
    await db.upgrade_to_premium(user_id, expires_at, tx_hash)

    await update.message.reply_text(
        "✅ *Payment confirmed! Welcome to Premium.*\n\n"
        f"Amount: {value_cusd:.2f} cUSD\n"
        f"Plan: {days}-day Premium\n"
        f"Expires: {expires_at.strftime('%b %d, %Y at %H:%M UTC')}\n"
        f"Transaction: `{tx_hash}`\n\n"
        "You now have unlimited AI asks with llama-3.3-70b-versatile.",
        parse_mode=ParseMode.MARKDOWN,
    )

    logger.info(
        "[PAYMENT] cUSD received from %s | value=%.4f cUSD | tx=%s | user=%d | premium until %s",
        result["from"],
        value_cusd,
        tx_hash,
        user_id,
        expires_at,
    )


# ── /digest ───────────────────────────────────────────────────────────────────


async def _safe_edit(message, text: str) -> None:
    """Attempt to edit a Telegram message; silently ignores failures to avoid masking the original error."""
    try:
        await message.edit_text(text)
    except Exception:
        pass


async def digest_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /digest — send personalized digest on demand (P22)."""
    user_id = update.effective_user.id
    loading_msg = None
    cache_hit = False

    # Step 1 — Rate limit check (sync, no try needed)
    if not rate_limiter.check_digest(user_id):
        await update.message.reply_text(
            "⏳ You already received today's digest. Next scheduled: 08:30 (Europe/Madrid)."
        )
        return

    # Step 2 — Send loading indicator
    loading_msg = await update.message.reply_text("⏳ Fetching your personalized digest...")

    # Step 3 — Cache check and fetch
    snapshot_path = "data/cache/full_snapshot.json"
    cache_hit = (
        os.path.exists(snapshot_path)
        and (time.time() - os.path.getmtime(snapshot_path)) < 1800
    )
    if not cache_hit:
        try:
            await fetcher_manager.fetch_all_sources()
        except Exception as exc:
            logger.warning("[DIGEST] Fetch failed, proceeding with stale cache | error: %s", exc)

    # Step 4a — Load user app preferences
    try:
        user_apps = await db.get_user_apps_by_category(user_id)
    except Exception as exc:
        logger.error("[DIGEST] DB error loading user apps for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Could not load your preferences. Please try again later.")
        return

    # Step 4b — Generate digest
    try:
        result = await digest_generator.generate_digest("daily", user_apps_by_category=user_apps)
        digest_text = result["text"]
        digest_id = result["digest_id"]
        sections = result.get("sections", 0)
        tokens = result.get("tokens", 0)
    except RuntimeError as exc:
        logger.error("[DIGEST] All Groq models failed for user %s | error: %s", user_id, exc)
        await _safe_edit(
            loading_msg,
            "❌ The AI service is temporarily unavailable. Please try again in a few minutes.",
        )
        return
    except KeyError as exc:
        logger.error("[DIGEST] Unexpected digest result format for user %s | missing key: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to generate digest. Please try again later.")
        return
    except Exception as exc:
        logger.error("[DIGEST] DigestGenerator failed for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to generate digest. Please try again later.")
        return

    # Step 5 — Send digest to user
    delivery_ok = False
    try:
        await loading_msg.edit_text(
            text=digest_text,
            reply_markup=get_digest_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
        delivery_ok = True
    except telegram.error.BadRequest as exc:
        logger.warning("[DIGEST] edit_text failed (BadRequest) for user %s | error: %s", user_id, exc)
        try:
            await loading_msg.edit_text(
                text=digest_text[:4000],  # safe margin below Telegram's 4096-char limit
                reply_markup=get_digest_keyboard(digest_id),
            )
            delivery_ok = True  # fallback also counts as successful delivery
        except Exception:
            await _safe_edit(loading_msg, "❌ Failed to send digest. Please try again later.")
            return
    except Exception as exc:
        logger.error("[DIGEST] Telegram API error sending digest to user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to send digest. Please try again later.")
        return

    # Activate cooldown only after confirmed delivery (any successful path)
    if delivery_ok:
        rate_limiter.register_digest(user_id)

    # Step 6 — Final log
    logger.info(
        "[DIGEST] Manual request from user %s | cache_hit: %s | sections: %s | tokens: %s",
        user_id,
        cache_hit,
        sections,
        tokens,
    )


# ── /settings ─────────────────────────────────────────────────────────────────


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings — show per-app toggle keyboard with live DB state."""
    user = update.effective_user
    user_id = user.id

    # Ensure user record exists so FK constraints are satisfied on first toggle
    await db.get_or_create_user(user_id, user.username, user.first_name)

    user_apps = await db.get_user_apps_by_category(user_id)
    await update.message.reply_text(
        text="⚙️ <b>Up-to-Celo — Select Your Apps</b>\n\nTap an app to toggle it on/off.",
        reply_markup=get_settings_keyboard(user_apps),
        parse_mode=ParseMode.HTML,
    )
    logger.info("[SETTINGS] /settings opened by user %d", user_id)


# ── /ask ──────────────────────────────────────────────────────────────────────


def _load_latest_digest_context() -> str:
    """Load the most recent digest from cache as context for the AI.

    Returns:
        Digest text from the latest JSON file in data/cache/digest, or a fallback string.
    """
    digest_dir = "data/cache/digest"
    files = sorted(glob.glob(f"{digest_dir}/*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        return "No recent digest available."
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("text", "No digest text found.")
    except Exception:
        return "Digest context unavailable."


def _is_session_expired(session: dict) -> bool:
    """Return True if the session has been inactive for more than 10 minutes."""
    return (time.time() - session.get("last_active", 0)) > 600


def _build_ask_messages(session: dict, new_question: str) -> list[dict]:
    """Build full message list: system prompt + conversation history + new question."""
    system_msg = {
        "role": "system",
        "content": (
            "You are Up-to-Celo, an AI assistant specialized in the Celo blockchain ecosystem. "
            "Answer questions based on the digest context and conversation history. "
            "Be concise, factual, and helpful. Do not speculate beyond the provided context. "
            "If the question is unrelated to Celo, politely redirect.\n\n"
            f"Latest digest context:\n{session['digest_context']}"
        ),
    }
    history = session.get("history", [])
    user_msg = {"role": "user", "content": new_question}
    return [system_msg] + history + [user_msg]


def _update_session(session: dict, question: str, answer: str) -> None:
    """Append the new exchange and enforce max 5 exchanges (10 messages)."""
    session["history"].append({"role": "user", "content": question})
    session["history"].append({"role": "assistant", "content": answer})
    if len(session["history"]) > 10:
        session["history"] = session["history"][-10:]
    session["last_active"] = time.time()


async def _process_ask(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    session: dict,
) -> None:
    """Shared core logic for /ask command and free-text continuation."""
    user_id = update.effective_user.id
    is_premium_user = await db.is_premium(user_id)
    tier = "premium" if is_premium_user else "free"
    model = "llama-3.3-70b-versatile" if is_premium_user else "llama-3.1-8b-instant"

    if not rate_limiter.check_ask(user_id, is_premium=is_premium_user):
        await update.message.reply_text(
            "⏳ Daily ask limit reached (3/day on Free plan).\n\n"
            "Upgrade to Premium for unlimited queries: /premium",
            parse_mode=ParseMode.HTML,
        )
        return

    loading_msg = await update.message.reply_text("🤖 Thinking...")
    messages = _build_ask_messages(session, question)

    try:
        response_text = await groq_client.generate(
            messages=messages, max_tokens=200, model=model
        )
    except RuntimeError as exc:
        logger.error("[ASK] All Groq models failed for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ AI service temporarily unavailable. Please try again later.")
        return
    except Exception as exc:
        logger.error("[ASK] Unexpected error for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to process your question. Please try again.")
        return

    escaped = escape_markdown(response_text, version=2)
    try:
        await loading_msg.edit_text(escaped, parse_mode=ParseMode.MARKDOWN_V2)
    except telegram.error.BadRequest:
        await loading_msg.edit_text(response_text)

    _update_session(session, question, response_text)
    rate_limiter.register_ask(user_id)

    logger.info(
        "[ASK] user=%s | tier=%s | model=%s | history_len=%s",
        user_id, tier, model, len(session["history"]),
    )


async def ask_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask — start or continue conversational session, answer via Groq (P26/P27)."""
    question = " ".join(context.args).strip() if context.args else ""
    if not question:
        await update.message.reply_text(
            "💬 Usage: /ask <i>your question about Celo</i>\n\n"
            "Example: <code>/ask What is MiniPay?</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    user_id = update.effective_user.id
    session = context.user_data.get("ask_session")

    if session is None or _is_session_expired(session):
        session = {
            "history": [],
            "last_active": time.time(),
            "digest_context": _load_latest_digest_context(),
        }
        context.user_data["ask_session"] = session
        logger.info("[ASK] New session started for user %s", user_id)
    else:
        logger.info(
            "[ASK] Continuing session for user %s | history_len=%s",
            user_id, len(session["history"]),
        )

    await _process_ask(update, context, question, session)


async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages when an ask session is active (continue conversation)."""
    session = context.user_data.get("ask_session")
    if session is None:
        return
    if _is_session_expired(session):
        del context.user_data["ask_session"]
        return

    question = (update.message.text or "").strip()
    if not question:
        return

    await _process_ask(update, context, question, session)


async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End the active ask session for the user."""
    if "ask_session" in context.user_data:
        del context.user_data["ask_session"]
        await update.message.reply_text(
            "✅ Conversation ended. Start a new one anytime with /ask.",
        )
    else:
        await update.message.reply_text(
            "No active conversation to end.",
        )


# ── inline query ──────────────────────────────────────────────────────────────

# Requires Inline Mode enabled in BotFather:
# /mybots → Bot Settings → Inline Mode → Enable

def _load_snapshot() -> dict | None:
    """Load the latest full snapshot from cache."""
    snapshot_path = "data/cache/full_snapshot.json"
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries — filter RSS/Twitter items by app name or title.

    Returns up to 5 InlineQueryResultArticle results matching the typed query.
    Empty query shows the 5 most recent items from the snapshot.
    """
    query_text = (update.inline_query.query or "").strip().lower()
    results: list[InlineQueryResultArticle] = []

    snapshot = _load_snapshot()
    if not snapshot:
        await update.inline_query.answer([], cache_time=10)
        return

    all_items: list[dict] = snapshot.get("rss", []) + snapshot.get("twitter", [])

    if not query_text:
        items_to_show = all_items[:5]
    else:
        items_to_show = [
            item for item in all_items
            if query_text in item.get("source_app", "").lower()
            or query_text in item.get("title", "").lower()
        ][:5]

    for item in items_to_show:
        title = item.get("title", "No title")
        url = item.get("url", "")
        source = item.get("source", item.get("source_app", "Unknown"))
        published = item.get("published", "")

        body = f"<b>{title}</b>\n"
        if published:
            body += f"🕐 {published}\n"
        body += f"📌 {source}\n"
        if url:
            body += f"\n🔗 <a href='{url}'>Read more</a>"

        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=title,
                description=f"{source} — {published}" if published else source,
                input_message_content=InputTextMessageContent(
                    message_text=body,
                    parse_mode=ParseMode.HTML,
                ),
                url=url or None,
            )
        )

    await update.inline_query.answer(results, cache_time=30)
    logger.info("[INLINE] query='%s' | results=%d", query_text, len(results))


# ── CommandHandler exports ─────────────────────────────────────────────────────

start_handler = CommandHandler("start", start_handler)
help_handler = CommandHandler("help", help_handler)
status_handler = CommandHandler("status", status_handler)
premium_handler = CommandHandler("premium", premium_handler)
confirm_payment_handler = CommandHandler("confirmpayment", confirm_payment_handler)
digest_handler = CommandHandler("digest", digest_command_handler)
settings_handler = CommandHandler("settings", settings_handler)
ask_handler = CommandHandler("ask", ask_command_handler)
stop_handler = CommandHandler("stop", stop_handler)
subscribe_handler = CommandHandler("subscribe", subscribe_handler)
unsubscribe_handler = CommandHandler("unsubscribe", unsubscribe_handler)
inline_handler = InlineQueryHandler(inline_query_handler)
