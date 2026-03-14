from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from src.bot.keyboards import get_main_keyboard, get_premium_keyboard  # P7 — not yet implemented
from src.database.manager import db
from src.fetchers.payment_fetcher import PaymentFetcher  # P-onchain — not yet implemented
from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail

logger = logging.getLogger(__name__)

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

WELCOME_MESSAGE = (
    "👋 Welcome to *Up-to-Celo*!\n\n"
    "Be up-to-date on the Celo Blockchain — daily AI digests delivered at 08:30 BRT.\n\n"
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
    "/ask <question> — Ask AI about Celo ecosystem\n"
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
        "✅ You're subscribed! You'll receive daily Celo digests at 08:30 BRT.",
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
            "🕐 Next digest: today at 08:30 BRT"
        )
    else:
        message = (
            "📊 *Your Up-to-Celo Status*\n\n"
            "Plan: 🆓 Free\n"
            "AI model: llama-3.1-8b-instant (3 asks/day)\n\n"
            "🕐 Next digest: today at 08:30 BRT\n\n"
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
# TODO: implement (P22 — manual digest on demand)


async def digest_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /digest — placeholder until P22."""
    await update.message.reply_text(
        "📰 *Digest*\n\nComing soon. Use /subscribe to get daily digests at 08:30 BRT.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /settings ─────────────────────────────────────────────────────────────────
# TODO: implement (P25 — app selection by category)


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings — placeholder until P25."""
    await update.message.reply_text(
        "⚙️ *Settings*\n\nApp filters coming soon. You will be able to choose "
        "which apps to see in your digest.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /ask ──────────────────────────────────────────────────────────────────────
# TODO: implement (P26 — ask AI about Celo)


async def ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask — placeholder until P26."""
    await update.message.reply_text(
        "🤖 *Ask*\n\nAsk me anything about Celo — coming soon. "
        "Example: /ask What is MiniPay?",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── CommandHandler exports ─────────────────────────────────────────────────────

start_handler = CommandHandler("start", start_handler)
help_handler = CommandHandler("help", help_handler)
status_handler = CommandHandler("status", status_handler)
premium_handler = CommandHandler("premium", premium_handler)
confirm_payment_handler = CommandHandler("confirmpayment", confirm_payment_handler)
digest_handler = CommandHandler("digest", digest_handler)
settings_handler = CommandHandler("settings", settings_handler)
ask_handler = CommandHandler("ask", ask_handler)
subscribe_handler = CommandHandler("subscribe", subscribe_handler)
unsubscribe_handler = CommandHandler("unsubscribe", unsubscribe_handler)
