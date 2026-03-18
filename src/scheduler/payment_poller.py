# src/scheduler/payment_poller.py
# Celo GovAI Hub — Background job that monitors the bot wallet for incoming CELO
# transfers and activates Premium automatically for registered users.

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from web3 import Web3

from src.utils.cache_manager import cache
from src.utils.env_validator import get_env_or_fail

logger = logging.getLogger(__name__)

# TTL for last_payment_block: 30 days (never expires in practice)
_LAST_BLOCK_TTL_MINUTES = 43200

# ERC-20 Transfer event: keccak256("Transfer(address,address,uint256)")
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# CELO GoldToken ERC-20 contract on Celo mainnet (token duality)
_CELO_CONTRACT = Web3.to_checksum_address("0x471EcE3750Da237f93B8E339c536989b8978a438")

# Plan thresholds in CELO
_PLAN_7D_CELO = 7.0
_PLAN_30D_CELO = 20.0


def _pad_address(address: str) -> str:
    """Pad a checksummed address to a 32-byte topic (0x + 64 lowercase hex chars)."""
    return "0x" + address[2:].lower().zfill(64)


def _fetch_logs_sync(from_block: int | None) -> tuple[list, int] | None:
    """Perform all blocking web3 calls to fetch new Transfer logs.

    Args:
        from_block: Last processed block + 1; if None, uses current_block - 100.

    Returns:
        (logs, current_block) tuple, or None if no new blocks or on error.
    """
    try:
        rpc_url = get_env_or_fail("CELO_RPC_URL")
        bot_wallet = Web3.to_checksum_address(get_env_or_fail("BOT_WALLET_ADDRESS"))
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

        current_block = w3.eth.block_number
        if from_block is None:
            from_block = max(0, current_block - 100)

        if from_block >= current_block:
            logger.debug("[POLLER] No new blocks since last run (block %s)", current_block)
            return None

        logger.debug(
            "[POLLER] Scanning blocks %s → %s for CELO transfers to bot wallet",
            from_block,
            current_block,
        )

        logs = w3.eth.get_logs({
            "address": _CELO_CONTRACT,
            "topics": [
                _TRANSFER_TOPIC,
                None,                       # any sender
                _pad_address(bot_wallet),   # recipient = bot wallet
            ],
            "fromBlock": from_block,
            "toBlock": current_block,
        })

        return list(logs), current_block

    except Exception as exc:
        logger.error("[POLLER] Web3 fetch failed: %s", exc, exc_info=True)
        return None


async def run_payment_poller(db, bot) -> None:
    """Scan new blocks for incoming CELO transfers to the bot wallet.

    Activates Premium automatically for users who have registered a personal
    wallet via /setwallet. Called by APScheduler every 60 seconds.

    Args:
        db: DatabaseManager singleton.
        bot: python-telegram-bot Bot instance (for sending notifications).
    """
    cached = await cache.get("last_payment_block")
    from_block = cached["block"] if cached else None

    # Run all blocking web3 calls in a thread pool to avoid stalling the event loop
    result = await asyncio.to_thread(_fetch_logs_sync, from_block)
    if result is None:
        return

    logs, current_block = result
    next_block = current_block + 1

    if not logs:
        logger.debug("[POLLER] No incoming CELO transfers found this cycle")
        await cache.set(
            "last_payment_block",
            {"block": next_block},
            ttl_minutes=_LAST_BLOCK_TTL_MINUTES,
        )
        return

    logger.info("[POLLER] Found %d Transfer log(s) to process", len(logs))

    for log in logs:
        await _process_transfer_log(log, db, bot)

    # Persist progress — next run starts from the block after this one
    await cache.set(
        "last_payment_block",
        {"block": next_block},
        ttl_minutes=_LAST_BLOCK_TTL_MINUTES,
    )


async def _process_transfer_log(log: dict, db, bot) -> None:
    """Process a single ERC-20 Transfer log entry.

    Args:
        log: Raw log dict from w3.eth.get_logs.
        db: DatabaseManager singleton.
        bot: Telegram Bot instance.
    """
    try:
        tx_hash = log["transactionHash"].hex()
        topics = log.get("topics", [])

        if len(topics) < 3:
            logger.debug("[POLLER] Skipping log with fewer than 3 topics | tx=%s", tx_hash)
            return

        # topics[1] = from address (padded to 32 bytes), take last 20 bytes
        from_raw = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
        from_address = Web3.to_checksum_address("0x" + from_raw[-40:])

        data = log["data"]
        data_hex = data.hex() if hasattr(data, "hex") else str(data)
        if not data_hex or data_hex in ("0x", ""):
            logger.debug("[POLLER] Empty data field in log | tx=%s", tx_hash)
            return

        amount_wei = int(data_hex, 16)
        amount_celo = amount_wei / (10 ** 18)

    except Exception as exc:
        logger.warning("[POLLER] Failed to decode log | error: %s", exc)
        return

    logger.info(
        "[POLLER] Incoming transfer | from=%s | amount=%.4f CELO | tx=%s",
        from_address,
        amount_celo,
        tx_hash,
    )

    # Replay protection — skip transactions already used to activate Premium
    if await db.is_tx_hash_used(tx_hash):
        logger.debug("[POLLER] Tx already processed: %s", tx_hash)
        return

    # Lookup which user registered this sending wallet
    user_id = await db.get_user_by_wallet(from_address)
    if not user_id:
        logger.info(
            "[POLLER] No registered user for wallet %s — skipping (use /confirmpayment)",
            from_address,
        )
        return

    # Determine plan by amount
    if amount_celo >= _PLAN_30D_CELO:
        days = 30
        label = "30-day Premium"
    elif amount_celo >= _PLAN_7D_CELO:
        days = 7
        label = "7-day Premium"
    else:
        logger.info(
            "[POLLER] Insufficient amount %.4f CELO from user %s", amount_celo, user_id
        )
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"Payment received: {amount_celo:.4f} CELO\n\n"
                    f"This amount is below the minimum required:\n"
                    f"7-day Premium:  {_PLAN_7D_CELO:.0f} CELO\n"
                    f"30-day Premium: {_PLAN_30D_CELO:.0f} CELO\n\n"
                    f"Please send the remaining amount to complete your plan."
                ),
            )
        except Exception as exc:
            logger.warning("[POLLER] Failed to notify user %s of low amount: %s", user_id, exc)
        return

    # Activate Premium
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await db.set_premium(user_id, expires_at=expires_at, tx_hash=tx_hash)

    logger.info(
        "[POLLER] Premium activated | user=%s | plan=%s | amount=%.4f CELO | expires=%s",
        user_id,
        label,
        amount_celo,
        expires_at.strftime("%Y-%m-%d"),
    )

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"Premium activated!\n\n"
                f"Plan: {label}\n"
                f"Amount received: {amount_celo:.4f} CELO\n"
                f"Expires: {expires_at.strftime('%Y-%m-%d')}\n\n"
                f"You now have unlimited AI queries.\n"
                f"Enjoy Celo GovAI Hub Premium!"
            ),
        )
    except Exception as exc:
        logger.warning("[POLLER] Failed to notify user %s of activation: %s", user_id, exc)
