from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import wraps
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import uuid

import telegram.error

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, InlineQueryHandler, ContextTypes
from telegram.helpers import escape_markdown

from web3 import Web3

from src.ai.digest_generator import digest_generator
from src.ai.groq_client import groq_client
from src.bot.keyboards import (
    get_digest_keyboard,
    get_main_keyboard,
    get_premium_keyboard,
    get_settings_keyboard,
)
from src.database.manager import DatabaseManager, db
from src.database.models import GovernanceAlert
from src.fetchers.fetcher_manager import fetcher_manager
from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail
from src.utils.cache_manager import cache
from src.utils.rate_limiter import rate_limiter

# ── CELO payment verification constants ───────────────────────────────────────

# CELO GoldToken ERC-20 contract — same address as native CELO on Celo L2 (token duality)
CELO_CONTRACT_ADDRESS = Web3.to_checksum_address("0x471EcE3750Da237f93B8E339c536989b8978a438")

# keccak256("Transfer(address,address,uint256)") — ERC-20 Transfer event signature
ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# CELO has 18 decimal places (same as ETH/ERC-20 standard)
CELO_DECIMALS = 18

# Minimum accepted amounts per plan (CELO)
PLAN_7D_CELO = 7.0    # 7-day Premium  — 7 CELO
PLAN_30D_CELO = 20.0  # 30-day Premium — 20 CELO

# Minimum on-chain confirmations before accepting a payment
MIN_CONFIRMATIONS = 3

logger = logging.getLogger(__name__)

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


# ── Governance helpers ──────────────────────────────────────────────────────────


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


def _format_governance_list(alerts: list[GovernanceAlert]) -> str:
    """Format a list of governance alerts for the /governance command."""
    if not alerts:
        return (
            "🏛️ *Celo Governance*\n\n"
            "No governance proposals found yet\\.\n\n"
            "Alerts are sent automatically when new proposals appear\\."
        )

    lines = ["🏛️ *Recent Celo Governance*\n"]
    for alert in alerts:
        proposer_short = _shorten_address(alert.proposer)
        queued_relative = _format_relative_time(alert.queued_at)
        celoscan_url = f"https://celoscan.io/tx/{alert.tx_hash}"

        lines.append(
            f"📋 *\\#{alert.proposal_id}* — Queued {queued_relative}\n"
            f"👤 Proposer: `{proposer_short}`\n"
            f"[🔗 CeloScan]({celoscan_url}) · "
            "[📋 Forum](https://forum.celo.org/c/governance)\n"
        )

    lines.append(
        "\n_→ Alerts are sent automatically when new proposals appear\\._"
    )
    return "\n".join(lines)


# ── Admin guard ───────────────────────────────────────────────────────────────

def admin_only(handler):
    """Decorator that restricts a handler to ADMIN_CHAT_ID only."""
    @wraps(handler)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        admin_id = int(get_env_or_fail("ADMIN_CHAT_ID"))
        if update.effective_user.id != admin_id:
            logger.warning(
                "[ADMIN] Unauthorized access attempt | user=%s | command=%s",
                update.effective_user.id,
                update.message.text if update.message else "unknown",
            )
            await update.message.reply_text("⛔ Unauthorized")
            return
        return await handler(update, context)
    return wrapper

_MAIN_MESSAGE = (
    "Welcome to Up-to-Celo AI! 🟡\n\n"
    "Stay up-to-date on the Celo blockchain with daily AI-powered digests.\n"
    "Covering: network updates, DeFi, ReFi, governance & live on-chain data.\n\n"
    "📰 /digest — Get today's Celo digest\n"
    "🤖 /ask — Chat with the Celo AI agent\n"
    "   Ex: /ask What's new in Ubeswap?\n"
    "   Ex: /ask How to vote on governance?\n"
    "🗳️ /governance — Latest Celo proposals\n"
    "⚙️ /settings — Customize your feed\n"
    "⭐️ /premium — Upgrade with CELO\n\n"
    "→ Start with /digest"
)

WELCOME_MESSAGE = _MAIN_MESSAGE
HELP_MESSAGE = _MAIN_MESSAGE

PREMIUM_MESSAGE = (
    "Premium — Up-to-Celo\n\n"
    "Unlock unlimited AI queries and the best Celo insights.\n\n"
    f"7-day Premium  — {PLAN_7D_CELO:.0f} CELO\n"
    f"30-day Premium — {PLAN_30D_CELO:.0f} CELO\n\n"
    "Send CELO to:\n"
    "{BOT_WALLET}\n\n"
    "Send from a personal wallet (MiniPay, Valora, MetaMask).\n"
    "Exchanges use intermediate addresses and won't be detected.\n\n"
    "After sending, tap the button below or use:\n"
    "/confirmpayment [tx_hash]"
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
    )


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(HELP_MESSAGE, reply_markup=get_main_keyboard())


# ── /subscribe / /unsubscribe ──────────────────────────────────────────────────


def _next_digest_str() -> str:
    """
    Return a human-readable string with the next digest delivery time.
    Digest is sent daily at 08:30 Europe/Madrid.
    """
    madrid_tz = ZoneInfo("Europe/Madrid")
    now_madrid = datetime.now(madrid_tz)
    target = now_madrid.replace(hour=8, minute=30, second=0, microsecond=0)

    if now_madrid >= target:
        target = target + timedelta(days=1)

    return target.strftime("%a, %b %d at %H:%M CET (Europe/Madrid)")


async def subscribe_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /subscribe — enable daily digest (idempotent)."""
    user_id = update.effective_user.id
    await db.get_or_create_user(
        user_id,
        update.effective_user.username,
        update.effective_user.first_name,
    )

    user = await db.get_user(user_id)
    if user and user.subscribed:
        await update.message.reply_text(
            "You are already subscribed to the daily digest.\n\n"
            f"Next digest: {_next_digest_str()}\n\n"
            "Use /unsubscribe to stop receiving digests."
        )
        logger.info("[SUBSCRIBE] Already subscribed | user=%s", user_id)
        return

    await db.update_subscription(user_id, True)
    logger.info("[SUBSCRIBE] User subscribed | user=%s", user_id)

    await update.message.reply_text(
        "You are now subscribed to Up-to-Celo!\n\n"
        f"Next digest: {_next_digest_str()}\n\n"
        "You will receive a daily AI-powered Celo digest automatically.\n\n"
        "Commands:\n"
        "/digest — Get today's digest now\n"
        "/settings — Customize which apps you follow\n"
        "/unsubscribe — Stop receiving digests"
    )


async def unsubscribe_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /unsubscribe — disable daily digest (idempotent)."""
    user_id = update.effective_user.id

    user = await db.get_user(user_id)
    if user and not user.subscribed:
        await update.message.reply_text(
            "You are not subscribed to the daily digest.\n\n"
            "Use /subscribe to start receiving daily Celo updates.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "↩️ Re-subscribe", callback_data="resubscribe"
                    )
                ]
            ]),
        )
        logger.info("[UNSUBSCRIBE] Already unsubscribed | user=%s", user_id)
        return

    await db.update_subscription(user_id, False)
    logger.info("[UNSUBSCRIBE] User unsubscribed | user=%s", user_id)

    await update.message.reply_text(
        "You have been unsubscribed from the daily digest.\n\n"
        "You will no longer receive automatic digests.\n\n"
        "You can still use:\n"
        "/digest — Get today's digest manually\n"
        "/ask — Chat with the Celo AI agent",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ Re-subscribe", callback_data="resubscribe"
                )
            ]
        ]),
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
            "Your Up-to-Celo Status\n\n"
            "Plan: Premium\n"
            f"Expires: {premium_expires_at.strftime('%b %d, %Y')}\n"
            "AI model: llama-3.3-70b-versatile (unlimited asks)\n\n"
            "Next digest: today at 08:30 CET (Europe/Madrid)"
        )
    else:
        message = (
            "Your Up-to-Celo Status\n\n"
            "Plan: Free\n"
            "AI model: llama-3.1-8b-instant (3 asks/day)\n\n"
            "Next digest: today at 08:30 CET (Europe/Madrid)\n\n"
            "Upgrade with /premium to unlock unlimited AI."
        )

    await update.message.reply_text(message)


# ── /premium ───────────────────────────────────────────────────────────────────

async def premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /premium — show plans with combined automatic + manual flow."""
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name

    is_premium = await db.is_premium(user_id)
    user_record = await db.get_or_create_user(user_id, username, first_name)
    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")

    if is_premium and getattr(user_record, "premium_expires_at", None):
        await update.message.reply_text(
            "⭐ You're already Premium!\n\n"
            "Enjoy unlimited AI asks with llama-3.3-70b-versatile.\n"
            "Use /status to check your expiration date.",
            reply_markup=get_premium_keyboard(),
        )
        return

    user_wallet = await db.get_wallet(user_id)
    wallet_line = (
        f"Registered wallet: {user_wallet}"
        if user_wallet
        else "No wallet registered yet. Use /setwallet 0xYourWallet"
    )

    await update.message.reply_text(
        f"Premium plans — Up-to-Celo\n\n"
        f"7-day Premium  — {PLAN_7D_CELO:.0f} CELO\n"
        f"30-day Premium — {PLAN_30D_CELO:.0f} CELO\n\n"
        f"Send CELO to:\n"
        f"{bot_wallet}\n\n"
        f"AUTOMATIC (personal wallet):\n"
        f"1. Register: /setwallet 0xYourWallet\n"
        f"2. Send CELO to the address above\n"
        f"3. Premium activates in ~60s automatically\n\n"
        f"MANUAL (exchange withdrawal):\n"
        f"Send CELO, then: /confirmpayment 0xTxHash\n\n"
        f"{wallet_line}",
        reply_markup=get_premium_keyboard(),
    )


# ── /setwallet ─────────────────────────────────────────────────────────────────

async def setwallet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setwallet <address> — register a personal wallet for automatic payment detection."""
    user_id = update.effective_user.id
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "Register your personal wallet to enable automatic Premium detection.\n\n"
            "Usage:\n"
            "/setwallet 0xYourWalletAddress\n\n"
            "Important: use a personal wallet (MiniPay, Valora, MetaMask).\n"
            "Exchange withdrawals cannot be detected automatically.\n"
            "For exchanges, use /confirmpayment instead."
        )
        return

    raw = args[0].strip()

    try:
        wallet = Web3.to_checksum_address(raw)
    except ValueError:
        await update.message.reply_text(
            "Invalid wallet address.\n\n"
            "Make sure it starts with 0x and has 42 characters."
        )
        return

    await db.get_or_create_user(user_id, update.effective_user.username, update.effective_user.first_name)
    await db.set_wallet(user_id, wallet)
    logger.info("[WALLET] Wallet registered | user=%s | wallet=%s", user_id, wallet)

    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")
    await update.message.reply_text(
        f"Wallet registered!\n\n"
        f"{wallet}\n\n"
        f"Now send {PLAN_7D_CELO:.0f} CELO (7-day) or {PLAN_30D_CELO:.0f} CELO (30-day) to:\n"
        f"{bot_wallet}\n\n"
        f"Premium will activate automatically within ~60 seconds after on-chain confirmation."
    )


# ── CELO on-chain verification ─────────────────────────────────────────────────


def _verify_celo_payment_sync(raw_input: str) -> dict | None:
    """Verify a CELO payment on the Celo blockchain (synchronous — use via asyncio.to_thread).

    Handles both ERC-20 Transfer events from the GoldToken contract (used by MiniPay,
    Valora and most Celo wallets) and native CELO transfers via tx.value, covering the
    full token duality of CELO on Celo L2.

    Args:
        raw_input: transaction hash (0x...) or Blockscout URL ending with the hash.

    Returns:
        Dict with keys ``amount_celo``, ``from_address``, ``confirmations``, ``tx_hash``,
        ``method`` (``"erc20"`` or ``"native"``) if a valid CELO transfer to the bot
        wallet is found, or None otherwise.
    """
    rpc_url = get_env_or_fail("CELO_RPC_URL")
    bot_wallet = Web3.to_checksum_address(get_env_or_fail("BOT_WALLET_ADDRESS"))
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    # Normalise — accept Blockscout URLs: https://celo.blockscout.com/tx/0xabc...
    tx_hash = raw_input.strip()
    if tx_hash.startswith("http"):
        tx_hash = tx_hash.rstrip("/").split("/")[-1]

    if not _TX_HASH_RE.match(tx_hash):
        logger.warning("[PAYMENT] Invalid tx hash format: %s", tx_hash)
        return None

    # Step 1 — Fetch receipt (confirms the tx was mined)
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception as exc:
        logger.warning("[PAYMENT] Receipt fetch failed for %s | error: %s", tx_hash, exc)
        return None

    if receipt is None:
        logger.warning("[PAYMENT] No receipt — tx may be pending: %s", tx_hash)
        return None

    # Step 2 — Transaction must have succeeded (status=1)
    if receipt.get("status") != 1:
        logger.warning("[PAYMENT] Transaction reverted: %s", tx_hash)
        return None

    # Step 3 — Minimum confirmations
    try:
        current_block = w3.eth.block_number
    except Exception as exc:
        logger.warning("[PAYMENT] Could not fetch block number: %s", exc)
        return None

    tx_block = receipt["blockNumber"]
    confirmations = current_block - tx_block + 1
    if confirmations < MIN_CONFIRMATIONS:
        logger.warning(
            "[PAYMENT] Insufficient confirmations: %d/%d | tx: %s",
            confirmations, MIN_CONFIRMATIONS, tx_hash,
        )
        return None

    # Step 4a — Parse ERC-20 Transfer logs from CELO GoldToken contract
    # (used by MiniPay, Valora and most Celo wallets)
    for log in receipt.get("logs", []):
        try:
            if Web3.to_checksum_address(log["address"]) != CELO_CONTRACT_ADDRESS:
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            # topics[0] = event signature, topics[1] = from, topics[2] = to
            sig = topics[0].hex() if hasattr(topics[0], "hex") else str(topics[0])
            if not sig.startswith("0x"):
                sig = "0x" + sig
            if sig.lower() != ERC20_TRANSFER_TOPIC.lower():
                continue

            to_raw = topics[2].hex() if hasattr(topics[2], "hex") else str(topics[2])
            to_address = Web3.to_checksum_address("0x" + to_raw[-40:])
            if to_address != bot_wallet:
                continue  # Transfer not directed to our wallet

            from_raw = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
            from_address = Web3.to_checksum_address("0x" + from_raw[-40:])

            data = log["data"]
            data_hex = data.hex() if hasattr(data, "hex") else str(data)
            if not data_hex or data_hex in ("0x", ""):
                continue
            amount_wei = int(data_hex, 16)
            amount_celo = amount_wei / (10 ** CELO_DECIMALS)

            logger.info(
                "[PAYMENT] ERC-20 CELO transfer confirmed | tx=%s | from=%s | "
                "amount=%.4f CELO | confirmations=%d",
                tx_hash, from_address, amount_celo, confirmations,
            )
            return {
                "amount_celo": amount_celo,
                "from_address": from_address,
                "confirmations": confirmations,
                "tx_hash": tx_hash,
                "method": "erc20",
            }
        except Exception as exc:
            logger.debug("[PAYMENT] Log parse error: %s", exc)
            continue

    # Step 4b — Fallback: native CELO transfer via tx.value
    # (some wallets send CELO as native value without going through GoldToken)
    try:
        tx = w3.eth.get_transaction(tx_hash)
        if (
            tx.get("to") is not None
            and Web3.to_checksum_address(tx["to"]) == bot_wallet
            and tx.get("value", 0) > 0
        ):
            amount_celo = tx["value"] / (10 ** CELO_DECIMALS)
            from_address = Web3.to_checksum_address(tx["from"])
            logger.info(
                "[PAYMENT] Native CELO transfer confirmed | tx=%s | from=%s | "
                "amount=%.4f CELO | confirmations=%d",
                tx_hash, from_address, amount_celo, confirmations,
            )
            return {
                "amount_celo": amount_celo,
                "from_address": from_address,
                "confirmations": confirmations,
                "tx_hash": tx_hash,
                "method": "native",
            }
    except Exception as exc:
        logger.warning("[PAYMENT] Native transfer check failed | tx=%s | error: %s", tx_hash, exc)

    logger.warning("[PAYMENT] No CELO transfer to bot wallet found in tx: %s", tx_hash)
    return None


# ── /confirmpayment ────────────────────────────────────────────────────────────

async def confirm_payment_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /confirmpayment <tx_hash|url> — verify CELO transfer on-chain and activate Premium."""
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    args = context.args or []

    if not args:
        await update.message.reply_text(
            "Please send your transaction hash or Blockscout URL.\n\n"
            "Example:\n"
            "/confirmpayment 0xabc123...\n\n"
            "Or paste the full URL:\n"
            "/confirmpayment https://celo.blockscout.com/tx/0xabc123...",
        )
        return

    raw_input = args[0].strip()
    logger.info("[PAYMENT] Confirm payment request | user=%s | input=%s", user_id, raw_input)

    loading_msg = await update.message.reply_text("🔍 Verifying your CELO payment on-chain...")

    # web3.py is synchronous — run in thread pool to avoid blocking the event loop
    payment = await asyncio.to_thread(_verify_celo_payment_sync, raw_input)

    if payment is None:
        await loading_msg.edit_text(
            "❌ Transaction not found or invalid.\n\n"
            "Make sure:\n"
            "1. The hash is correct (66 chars starting with 0x)\n"
            f"2. The transaction has at least {MIN_CONFIRMATIONS} confirmations\n"
            "3. You sent CELO to the bot wallet\n"
            "4. You sent from MiniPay, Valora, or MetaMask — not an exchange\n\n"
            "Check your tx on: https://celo.blockscout.com",
        )
        return

    amount = payment["amount_celo"]

    if amount >= PLAN_30D_CELO:
        days = 30
        label = "30-day Premium"
    elif amount >= PLAN_7D_CELO:
        days = 7
        label = "7-day Premium"
    else:
        await loading_msg.edit_text(
            f"❌ Payment too low: {amount:.4f} CELO received.\n\n"
            f"Minimum amounts:\n"
            f"7-day Premium:  {PLAN_7D_CELO:.0f} CELO\n"
            f"30-day Premium: {PLAN_30D_CELO:.0f} CELO\n\n"
            "Please send the correct amount and try again.",
        )
        logger.warning(
            "[PAYMENT] Insufficient amount | user=%s | amount=%.4f CELO", user_id, amount
        )
        return

    # Replay protection — each tx hash can only be used once
    if await db.is_tx_hash_used(payment["tx_hash"]):
        await loading_msg.edit_text(
            "❌ This transaction has already been used to activate Premium.\n\n"
            "If you believe this is an error, contact support.",
        )
        logger.warning(
            "[PAYMENT] Duplicate tx hash | user=%s | tx=%s", user_id, payment["tx_hash"]
        )
        return

    await db.get_or_create_user(user_id, username, first_name)

    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await db.set_premium(user_id, expires_at=expires_at, tx_hash=payment["tx_hash"])

    logger.info(
        "[PAYMENT] Premium activated | user=%s | plan=%s | amount=%.4f CELO | "
        "method=%s | expires=%s | tx=%s",
        user_id, label, amount, payment["method"],
        expires_at.strftime("%Y-%m-%d"), payment["tx_hash"],
    )

    await loading_msg.edit_text(
        f"✅ Premium activated!\n\n"
        f"Plan: {label}\n"
        f"Amount received: {amount:.4f} CELO\n"
        f"Expires: {expires_at.strftime('%Y-%m-%d')}\n"
        f"Confirmations: {payment['confirmations']}\n"
        f"Method: {payment['method']}\n\n"
        "You now have unlimited AI queries. Enjoy Up-to-Celo Premium!",
    )


# ── /digest ───────────────────────────────────────────────────────────────────


async def _safe_edit(message, text: str) -> None:
    """Attempt to edit a Telegram message; silently ignores failures to avoid masking the original error."""
    try:
        await message.edit_text(text)
    except Exception:
        pass


async def _safe_reply(message, text: str, reply_markup=None) -> None:
    """Send an HTML message with automatic plain-text fallback on parse errors.

    Use for any message that mixes static HTML tags with dynamic content
    (usernames, wallet addresses, dates, etc.) to guard against unexpected
    special characters triggering a BadRequest from the Telegram parser.
    """
    import re as _re
    try:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except telegram.error.BadRequest:
        plain = _re.sub(r"<[^>]+>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        await message.reply_text(plain, reply_markup=reply_markup)


async def digest_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /digest — send personalized digest on demand (P22)."""
    user_id = update.effective_user.id
    loading_msg = None
    cache_hit = False
    sections = 0
    tokens = 0

    # Step 1 — Rate limit check
    if not rate_limiter.check_digest(user_id):
        await update.message.reply_text(
            "⏳ You already received today's digest. Next scheduled: 08:30 CET (Europe/Madrid)."
        )
        return

    # Step 2 — Loading indicator
    loading_msg = await update.message.reply_text("⏳ Generating your Celo digest...")
    logger.info("[DIGEST] Starting manual request for user %s", user_id)

    # Step 3 — Cache check (snapshot TTL: 30 min)
    snapshot_cached = await cache.get_snapshot()
    cache_hit = snapshot_cached is not None
    logger.info("[DIGEST] Cache hit: %s for user %s", cache_hit, user_id)

    # Step 4 — Fetch with global timeout (skipped when cache is fresh)
    if not cache_hit:
        try:
            logger.info("[DIGEST] Fetching all sources for user %s...", user_id)
            await asyncio.wait_for(
                fetcher_manager.fetch_all_sources(),
                timeout=40.0,
            )
            logger.info("[DIGEST] Fetch complete for user %s", user_id)
        except asyncio.TimeoutError:
            logger.warning("[DIGEST] fetch_all_sources() timed out after 40s — proceeding with stale cache")
        except Exception as exc:
            logger.warning("[DIGEST] Fetch failed — proceeding with stale cache | error: %s", exc)

    # Step 5 — Load user app preferences
    try:
        logger.info("[DIGEST] Loading user apps for user %s...", user_id)
        user_apps = await db.get_user_apps_by_category(user_id)
        logger.info("[DIGEST] User apps loaded: %s categories for user %s", len(user_apps), user_id)
    except Exception as exc:
        logger.error("[DIGEST] DB error for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Could not load your preferences. Please try again later.")
        return

    # Step 6 — Generate digest with timeout
    try:
        logger.info("[DIGEST] Generating digest for user %s...", user_id)
        result = await asyncio.wait_for(
            digest_generator.generate_digest("daily", user_apps_by_category=user_apps),
            timeout=35.0,
        )
        digest_text = result["text"]
        digest_id = result["digest_id"]
        sections = result.get("sections", [])
        tokens = result.get("tokens", 0)
        sections_for_log = len(sections) if isinstance(sections, list) else sections
        logger.info(
            "[DIGEST] Digest generated | id=%s sections=%s tokens=%s user=%s",
            digest_id, sections_for_log, tokens, user_id,
        )
    except asyncio.TimeoutError:
        logger.error("[DIGEST] DigestGenerator timed out after 35s for user %s", user_id)
        await _safe_edit(loading_msg, "❌ Digest generation timed out. Please try again.")
        return
    except RuntimeError as exc:
        logger.error("[DIGEST] All Groq models failed for user %s | error: %s", user_id, exc)
        await _safe_edit(
            loading_msg,
            "❌ AI service temporarily unavailable. Please try again in a few minutes.",
        )
        return
    except KeyError as exc:
        logger.error("[DIGEST] Unexpected digest format for user %s | missing key: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to generate digest. Please try again later.")
        return
    except Exception as exc:
        logger.error("[DIGEST] DigestGenerator failed for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to generate digest. Please try again later.")
        return

    # Step 7 — Send digest to user
    delivery_ok = False
    try:
        await loading_msg.edit_text(
            text=digest_text,
            reply_markup=get_digest_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
        delivery_ok = True
    except telegram.error.BadRequest as exc:
        logger.warning("[DIGEST] edit_text BadRequest for user %s | error: %s", user_id, exc)
        try:
            await loading_msg.edit_text(
                text=digest_text[:4000],  # safe margin below Telegram's 4096-char limit
                reply_markup=get_digest_keyboard(digest_id),
            )
            delivery_ok = True
        except Exception:
            await _safe_edit(loading_msg, "❌ Failed to send digest. Please try again later.")
            return
    except Exception as exc:
        logger.error("[DIGEST] Telegram error for user %s | error: %s", user_id, exc)
        await _safe_edit(loading_msg, "❌ Failed to send digest. Please try again later.")
        return

    # Activate cooldown only after confirmed delivery
    if delivery_ok:
        rate_limiter.register_digest(user_id)

    logger.info(
        "[DIGEST] Manual request done | user=%s cache_hit=%s sections=%s tokens=%s",
        user_id,
        cache_hit,
        len(sections) if isinstance(sections, list) else sections,
        tokens,
    )


# ── /settings ─────────────────────────────────────────────────────────────────


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings — show root menu with 4 categories."""
    user = update.effective_user
    user_id = user.id

    await db.get_or_create_user(user_id, user.username, user.first_name)

    user_apps = await db.get_user_apps_by_category(user_id)
    await update.message.reply_text(
        "⚙️ Up-to-Celo — Select Your Apps\n\n"
        "Tap a category to manage its apps.\n"
        "✅ = all enabled  ☑️ = some enabled  ☐ = none",
        reply_markup=get_settings_keyboard(user_apps),
    )
    logger.info("[SETTINGS] /settings opened by user %d", user_id)


# ── /ask ──────────────────────────────────────────────────────────────────────


async def _load_latest_digest_context() -> str:
    """Load the most recent digest from cache as context for the AI.

    Returns:
        Digest text from the latest cached digest, or a fallback string.
    """
    try:
        latest_id = cache.get_latest_digest_id()
        if not latest_id:
            logger.debug("[ASK] No digest in cache")
            return "No recent digest available."
        data = await cache.get_digest(latest_id)
        if not data:
            return "No recent digest available."
        text = data.get("text", "")
        logger.debug("[ASK] Loaded digest context | digest_id=%s | length=%s", latest_id, len(text))
        return text if text else "No recent digest available."
    except Exception as exc:
        logger.warning("[ASK] Failed to load digest context | error: %s", exc)
        return "Digest context unavailable."


def _is_session_expired(session: dict) -> bool:
    """Return True if the session has been inactive for more than 10 minutes."""
    return (time.time() - session.get("last_active", 0)) > 600


def _build_ask_messages(session: dict, new_question: str) -> list[dict]:
    """Build full message list: system prompt + conversation history + new question."""
    system_msg = {
        "role": "system",
        "content": (
            "You are Up-to-Celo, an enthusiastic AI agent and proud advocate of the "
            "Celo blockchain ecosystem. Your mission is to inform, inspire, and engage "
            "users about everything happening in the Celo world.\n\n"

            "Your personality:\n"
            "- You are genuinely excited about Celo's mission of financial inclusion\n"
            "- You highlight real opportunities in the ecosystem (MiniPay, cUSD, DeFi, ReFi)\n"
            "- You encourage users to explore, use, and participate in Celo apps\n"
            "- You are factual and grounded — always based on the digest context provided\n"
            "- You are concise but never cold — friendly, direct, and motivating\n"
            "- When relevant, you remind users that CELO has real utility and growing adoption\n"
            "- You never give direct financial advice or tell users to 'buy CELO' — instead, "
            "you highlight ecosystem developments, use cases, and on-chain activity that "
            "users can draw their own conclusions from\n\n"

            "Rules:\n"
            "- Always base your answers on the digest context below\n"
            "- If data is unavailable, say so clearly and suggest running /digest\n"
            "- If the question is unrelated to Celo or crypto, politely redirect\n"
            "- Keep responses under 400 tokens — be sharp and impactful\n\n"

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
            messages=messages, max_tokens=450, model=model
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

    # Always load freshest digest available — a new /digest may have run since session start
    fresh_context = await _load_latest_digest_context()

    if session is None or _is_session_expired(session):
        session = {
            "history": [],
            "last_active": time.time(),
            "digest_context": fresh_context,
        }
        context.user_data["ask_session"] = session
        logger.info("[ASK] New session started for user %s", user_id)
    else:
        # Refresh digest context in active sessions whenever a real digest is available
        if fresh_context != "No recent digest available.":
            session["digest_context"] = fresh_context
        logger.info(
            "[ASK] Continuing session for user %s | history_len=%s",
            user_id, len(session["history"]),
        )

    # Block only if no digest context exists after the refresh attempt
    if session["digest_context"] == "No recent digest available.":
        await update.message.reply_text(
            "📰 <b>No digest available yet!</b>\n\n"
            "Run /digest first to fetch the latest Celo updates — "
            "then I can give you sharp, data-backed answers about the ecosystem. 🚀",
            parse_mode=ParseMode.HTML,
        )
        return

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


async def governance_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /governance command — show recent governance proposals."""
    db_manager = DatabaseManager()
    alerts = await db_manager.get_recent_alerts(limit=5)

    text = _format_governance_list(alerts)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


# ── Admin commands (ADMIN_CHAT_ID only) ────────────────────────────────────────

@admin_only
async def admin_stats_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Send bot statistics to the admin."""
    total_subscribers = await db.count_subscribers()
    total_premium = await db.count_premium_users()
    digests_today = await db.count_digests_today()
    tokens_today = await db.sum_groq_tokens_today()
    errors_today = await db.count_errors_today()

    uptime_start = context.bot_data.get("uptime_start")
    if uptime_start:
        delta = datetime.now(timezone.utc) - uptime_start
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        uptime = f"{hours}h {minutes}m"
    else:
        uptime = "unknown"

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    await update.message.reply_text(
        "Up-to-Celo — Admin Stats\n"
        f"Generated: {now_utc}\n\n"
        "Users\n"
        f"  Subscribers:   {total_subscribers}\n"
        f"  Premium:       {total_premium}\n\n"
        "Today\n"
        f"  Digests sent:  {digests_today}\n"
        f"  Groq tokens:   {tokens_today:,}\n"
        f"  Errors:        {errors_today}\n\n"
        "System\n"
        f"  Uptime:        {uptime}\n"
        "  Version:       1.1"
    )
    logger.info("[ADMIN] Stats requested | user=%s", update.effective_user.id)


@admin_only
async def admin_broadcast_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Broadcast a free-text message to all active subscribers."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /admin_broadcast Your message here\n\n"
            "The message will be sent to all active subscribers."
        )
        return

    message_text = " ".join(args)
    subscribers = await db.get_all_subscribers()

    if not subscribers:
        await update.message.reply_text("No active subscribers found.")
        return

    confirm_msg = await update.message.reply_text(
        f"Broadcasting to {len(subscribers)} subscribers...\n\n"
        f"Message:\n{message_text}"
    )

    ok_count = 0
    error_count = 0

    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"Up-to-Celo Announcement\n\n{message_text}",
            )
            ok_count += 1
        except Exception as exc:
            error_count += 1
            logger.warning(
                "[ADMIN] Broadcast failed for user=%s | error=%s",
                user_id,
                exc,
            )

        await asyncio.sleep(1 / 30)

    logger.info(
        "[ADMIN] Broadcast complete | sent=%s errors=%s | admin=%s",
        ok_count,
        error_count,
        update.effective_user.id,
    )

    await confirm_msg.edit_text(
        "Broadcast complete!\n\n"
        f"Sent:   {ok_count}/{len(subscribers)}\n"
        f"Errors: {error_count}"
    )


@admin_only
async def admin_digest_now_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Force immediate digest delivery to all subscribers, bypassing schedule."""
    from src.scheduler.notifier import Notifier

    status_msg = await update.message.reply_text(
        "Forcing digest delivery to all subscribers..."
    )

    logger.info(
        "[ADMIN] Manual digest triggered | admin=%s",
        update.effective_user.id,
    )

    try:
        notifier = Notifier()
        result = await notifier.send_daily_digest(bot=context.bot)

        await status_msg.edit_text(
            "Digest sent!\n\n"
            f"Recipients: {result.get('recipients', 0)}\n"
            f"Groq tokens: {result.get('tokens', 0):,}\n"
            f"Errors: {result.get('errors', 0)}"
        )
        logger.info(
            "[ADMIN] Forced digest complete | result=%s",
            result,
        )

    except Exception as exc:
        logger.error(
            "[ADMIN] Forced digest failed | error=%s",
            exc,
            exc_info=True,
        )
        await status_msg.edit_text(
            f"Digest delivery failed.\n\nError: {exc}"
        )


# ── inline query ──────────────────────────────────────────────────────────────

# Requires Inline Mode enabled in BotFather:
# /mybots → Bot Settings → Inline Mode → Enable

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries — filter RSS/Twitter items by app name or title.

    Returns up to 5 InlineQueryResultArticle results matching the typed query.
    Empty query shows the 5 most recent items from the snapshot.
    """
    query_text = (update.inline_query.query or "").strip().lower()
    results: list[InlineQueryResultArticle] = []

    snapshot = await cache.get_snapshot()
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
setwallet_handler = CommandHandler("setwallet", setwallet_handler)
digest_handler = CommandHandler("digest", digest_command_handler)
settings_handler = CommandHandler("settings", settings_handler)
ask_handler = CommandHandler("ask", ask_command_handler)
stop_handler = CommandHandler("stop", stop_handler)
subscribe_handler = CommandHandler("subscribe", subscribe_handler)
unsubscribe_handler = CommandHandler("unsubscribe", unsubscribe_handler)
inline_handler = InlineQueryHandler(inline_query_handler)
governance_handler = CommandHandler("governance", governance_handler)
