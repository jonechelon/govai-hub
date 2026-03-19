# src/bot/callbacks.py
# Celo GovAI Hub — central callback router for inline keyboards (P8)

from __future__ import annotations

import json
import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

from web3 import Web3

from src.bot.handlers import (
    HELP_MESSAGE_TEXT,
    PLAN_30D_CUSD,
    PLAN_7D_CUSD,
    PREMIUM_MESSAGE,
    GOVERNANCE_HUB_MESSAGE,
    WELCOME_MESSAGE,
)
from src.bot.keyboards import (
    CATEGORY_DISPLAY,
    get_category_keyboard,
    get_details_keyboard,
    get_digest_keyboard,
    get_links_keyboard,
    get_main_keyboard,
    get_governance_keyboard,
    get_help_keyboard,
    get_wallet_keyboard,
    get_premium_keyboard,
    get_premium_plan_keyboard,
    get_settings_keyboard,
    governance_keyboard,
)
from src.database.manager import db
from src.database.models import APPS_AVAILABLE
from src.ai.digest_generator import digest_generator
from src.utils.cache_manager import cache
from src.utils.env_validator import get_env_or_fail
from src.fetchers.governance_fetcher import (
    GOVERNANCE_ADDRESS,
    get_active_proposals_onchain,
    get_historical_proposals_onchain,
)

logger = logging.getLogger(__name__)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks. MUST call query.answer() first (2.E1)."""
    query = update.callback_query
    await query.answer()  # MUST be first — prevents infinite loading spinner

    data: str = query.data or ""
    user_id: int = update.effective_user.id

    logger.info("[CALLBACK] %s from user %d", data, user_id)

    try:
        if data == "noop":
            return  # silently ignore category header buttons
        elif data == "start":
            await _handle_start(query, user_id)
        elif data == "menu:main":
            await _handle_start(query, user_id)
        elif data == "main_menu":
            await _handle_start(query, user_id)
        elif data == "governance:open":
            await _handle_governance_open(query)
        elif data == "governance_menu":
            await _handle_governance_open(query)
        elif data == "digest:latest":
            await _handle_digest_latest(query, context, user_id)
        elif data == "wallet:open":
            await _handle_wallet_open(query, user_id)
        elif data == "menu:premium":
            await _handle_premium_open(query, context, user_id)
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
        elif data.startswith("settings:category:"):
            cat_key = data.split(":", 2)[2]
            await _handle_settings_category(query, context, user_id, cat_key)
        elif data == "settings_close":
            await _handle_settings_close(query, user_id)
        elif data.startswith("toggle_app:"):
            await _handle_toggle_app(query, user_id)
        elif data == "notify:toggle":
            await _handle_notifications_toggle(query, user_id)
        elif data == "net:switch":
            await _handle_network_switch(query, user_id)
        elif data == "premium:open":
            await _handle_premium_open(query, context, user_id)
        elif data == "premium":
            await _handle_premium_open(query, context, user_id)
        elif data == "premium:7d":
            await _handle_premium_plan(query, user_id, days=7)
        elif data == "premium:30d":
            await _handle_premium_plan(query, user_id, days=30)
        elif data == "premium:confirm":
            await _handle_premium_confirm(query)
        elif data == "premium:back":
            await _handle_premium_back(query, user_id)
        elif data.startswith("back:"):
            await _handle_back(query, user_id)
        elif data == "help:open":
            await _handle_help_open(query, user_id)
        elif data == "help":
            await _handle_help_open(query, user_id)
        elif data == "govlist":
            await _handle_govlist(query)
        elif data == "govhistory":
            await _handle_govhistory(query)
        elif data == "govstatus":
            await _handle_govstatus(query, user_id)
        elif data == "gov:status":
            await _handle_govstatus(query, user_id)
        elif data == "resubscribe":
            await _handle_resubscribe(query, user_id)
        else:
            logger.warning(
                "[CALLBACK] Unknown callback_data: %s from user %d",
                data,
                user_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[CALLBACK] Error handling callback_data=%s for user=%d | error=%s",
            data,
            user_id,
            exc,
            exc_info=True,
        )
        try:
            await query.answer("Error loading digest", show_alert=True)
        except Exception:  # noqa: BLE001
            # Best-effort: avoid raising inside the error handler itself.
            pass


async def _handle_start(query, user_id: int) -> None:
    """Return to the main menu screen."""
    user_record = await db.get_user(user_id)
    preferred_network = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    notifications_enabled = getattr(user_record, "notifications_enabled", True)
    main_kb = get_main_keyboard(
        preferred_network=preferred_network,
        notifications_enabled=notifications_enabled,
    )
    try:
        await query.edit_message_text(
            WELCOME_MESSAGE,
            reply_markup=main_kb,
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        try:
            await query.message.reply_text(
                WELCOME_MESSAGE,
                reply_markup=main_kb,
                parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass


async def _handle_wallet_open(query, user_id: int) -> None:
    """Show wallet registration instructions (same intent as /setwallet without args)."""
    text = (
        "👛 Set Wallet\n\n"
        "Register your wallet to enable Premium auto-activation and governance delegation checks.\n\n"
        "Usage:\n"
        "/setwallet 0xYourWalletAddress\n\n"
        "Important: use a personal wallet (MiniPay, Valora, MetaMask).\n"
        "Exchange withdrawals cannot be detected automatically.\n"
        "For exchanges, use /confirmpayment instead."
    )

    user_record = await db.get_user(user_id)
    preferred_network = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    wallet_kb = get_wallet_keyboard(preferred_network)

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=wallet_kb,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        try:
            await query.message.reply_text(
                text=text,
                reply_markup=wallet_kb,
            )
        except Exception:  # noqa: BLE001
            pass


# ── digest ────────────────────────────────────────────────────────────────────

async def _handle_digest_latest(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Generate and show the latest digest when user taps the main menu button.

    Uses the same DigestGenerator pipeline as /digest, honoring the user's app
    preferences. Any failure is surfaced as a short alert to the user and
    logged with full traceback for debugging.
    """
    callback_data = query.data or ""
    logger.info("[CALLBACK] _handle_digest_latest | data=%s | user=%d", callback_data, user_id)

    loading_msg = await query.message.reply_text("⏳ Generating your Celo digest...")

    try:
        user_apps = await db.get_user_apps_by_category(user_id)
        logger.debug(
            "[DIGEST_CALLBACK] Loaded user apps for user=%d | categories=%d",
            user_id,
            len(user_apps),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[DIGEST_CALLBACK] Failed to load user apps | user=%d | error=%s",
            user_id,
            exc,
            exc_info=True,
        )
        try:
            await loading_msg.edit_text("❌ Failed to load digest. Try again.")
        except Exception:  # noqa: BLE001
            pass
        await query.answer("Error loading digest", show_alert=True)
        return

    try:
        result = await digest_generator.generate_digest(
            template="daily",
            user_apps_by_category=user_apps,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[DIGEST_CALLBACK] Digest generation failed for user=%d | error=%s",
            user_id,
            exc,
            exc_info=True,
        )
        try:
            await loading_msg.edit_text("❌ Failed to load digest. Try again.")
        except Exception:  # noqa: BLE001
            pass
        await query.answer("Error loading digest", show_alert=True)
        return

    text = result.get("text") or ""
    digest_id = result.get("digest_id")

    if not text or not digest_id:
        logger.error(
            "[DIGEST_CALLBACK] Invalid digest result for user=%d | keys=%s",
            user_id,
            list(result.keys()),
        )
        try:
            await loading_msg.edit_text("❌ Failed to load digest. Try again.")
        except Exception:  # noqa: BLE001
            pass
        await query.answer("Error loading digest", show_alert=True)
        return

    try:
        await loading_msg.edit_text(
            text=text,
            reply_markup=get_digest_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as exc:
        logger.warning(
            "[DIGEST_CALLBACK] BadRequest editing message for user=%d | error=%s",
            user_id,
            exc,
        )
        try:
            await loading_msg.edit_text(
                text=text[:4000],
                reply_markup=get_digest_keyboard(digest_id),
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.error(
                "[DIGEST_CALLBACK] Fallback edit failed for user=%d | error=%s",
                user_id,
                inner_exc,
                exc_info=True,
            )
            await query.answer("Error loading digest", show_alert=True)
            return
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[DIGEST_CALLBACK] Unexpected error editing message for user=%d | error=%s",
            user_id,
            exc,
            exc_info=True,
        )
        try:
            await loading_msg.edit_text("❌ Failed to load digest. Try again.")
        except Exception:  # noqa: BLE001
            pass
        await query.answer("Error loading digest", show_alert=True)
        return

    logger.info(
        "[DIGEST_CALLBACK] Digest loaded via main button | digest_id=%s | user=%d",
        digest_id,
        user_id,
    )


async def _handle_details(query, context: ContextTypes.DEFAULT_TYPE, digest_id: str) -> None:
    """Expand the digest message to full view with the details keyboard."""
    payload = await cache.get_digest(digest_id)
    if not payload:
        await query.answer("⚠️ Digest not found. It may have expired.", show_alert=True)
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
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise
    logger.info("[DETAILS] Digest %s loaded for user in details view", digest_id)


async def _extract_links_from_digest(digest_id: str) -> list[dict]:
    """
    Load digest from cache and extract all URLs with title and source.
    Returns a list of dicts: [{title, url, source}, ...]
    Limits to 15 links maximum.
    """
    data = await cache.get_digest(digest_id)
    if not data:
        logger.warning("[LINKS] Cache not found or expired | digest_id=%s", digest_id)
        return []

    try:
        sections = data.get("sections", [])
        links: list[dict] = []

        for section in sections:
            items = section.get("items", [])
            for item in items:
                url = item.get("url") or item.get("link") or ""
                if not url or not url.startswith("http"):
                    continue

                title = item.get("title") or item.get("name") or "No title"
                source = (
                    item.get("source")
                    or item.get("source_app")
                    or section.get("category", "")
                    or "Unknown"
                )

                if any(lk["url"] == url for lk in links):
                    continue

                links.append({
                    "title": title.strip() if isinstance(title, str) else "No title",
                    "url": url.strip(),
                    "source": source.strip() if isinstance(source, str) else str(source),
                })

                if len(links) >= 15:
                    return links

        return links

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error(
            "[LINKS] Failed to parse digest cache | digest_id=%s | error=%s",
            digest_id,
            exc,
        )
        return []


async def _handle_links(
    query, context: ContextTypes.DEFAULT_TYPE, digest_id: str
) -> None:
    """
    Handle links:{digest_id} callback.
    Extracts URLs from cached digest and displays a numbered list.
    """
    user_id = query.from_user.id if query.from_user else 0
    links = await _extract_links_from_digest(digest_id)

    logger.info(
        "[LINKS] Requested | user=%s | digest_id=%s | links_found=%s",
        user_id,
        digest_id,
        len(links),
    )

    if not links:
        try:
            await query.edit_message_text(
                "No links found for this digest.\n\n"
                "The digest cache may have expired (TTL: 24h).",
                reply_markup=get_links_keyboard(digest_id),
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
        return

    lines = [f"Links from this digest ({len(links)} found)\n"]

    for i, link in enumerate(links, start=1):
        title = (
            link["title"][:60] + "…"
            if len(link["title"]) > 60
            else link["title"]
        )
        source = link["source"]
        url = link["url"]
        lines.append(f"{i}. {title}\n   {source} — {url}\n")

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        full_text = full_text[:3950] + "\n\n… (truncated)"

    try:
        await query.edit_message_text(
            full_text,
            reply_markup=get_links_keyboard(digest_id),
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_back(query, user_id: int) -> None:
    """Return to digest original view with the standard digest keyboard."""
    digest_id = query.data.split(":", 1)[1]
    payload = await cache.get_digest(digest_id)
    if not payload:
        await query.answer("❌ Could not reload digest.", show_alert=True)
        return
    text = payload.get("text", "")
    if not text:
        await query.answer("❌ Could not reload digest.", show_alert=True)
        return
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_digest_keyboard(digest_id),
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise
    logger.info("[DETAILS] Back to digest view | digest=%s | user=%d", digest_id, user_id)


async def _handle_ask(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int, digest_id: str
) -> None:
    """Activate ask mode with digest context (AI logic in P26/P27)."""
    context.user_data["ask_digest_id"] = digest_id
    context.user_data["ask_active"] = True
    msg = (
        "🤖 Ask AI — Celo Ecosystem\n\n"
        "Ask anything about the Celo ecosystem.\n"
        "I'll use this digest as context.\n\n"
        "Type your question now, or use /ask [question]\n"
        "Type /stop to end this session."
    )
    await query.message.reply_text(msg)


# ── settings ───────────────────────────────────────────────────────────────────

async def _handle_settings_open(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Open root settings menu with 4 category buttons."""
    user_record = await db.get_user(user_id)
    preferred_network = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    notifications_enabled = getattr(user_record, "notifications_enabled", True)
    user_apps = await db.get_user_apps_by_category(user_id)
    text = (
        "⚙️ Celo GovAI Hub — Select Your Apps\n\n"
        "Also manage your governance network and vote alert preferences.\n\n"
        "Tap a category to manage its apps.\n"
        "✅ = all enabled  ☑️ = some enabled  ☐ = none"
    )
    try:
        await query.message.edit_text(
            text,
            reply_markup=get_settings_keyboard(
                user_apps,
                preferred_network=preferred_network,
                notifications_enabled=notifications_enabled,
            ),
        )
    except BadRequest:
        pass


async def _handle_settings_category(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int, cat_key: str
) -> None:
    """Show category submenu with app toggles for the selected category."""
    user_apps = await db.get_user_apps_by_category(user_id)
    emoji, label = CATEGORY_DISPLAY.get(cat_key, ("⚙️", cat_key))
    try:
        await query.message.edit_text(
            f"{emoji} {label} — tap to toggle apps",
            reply_markup=get_category_keyboard(cat_key, user_apps),
        )
    except BadRequest:
        pass


async def _handle_settings_close(query, user_id: int) -> None:
    """Close settings and show save confirmation."""
    user_record = await db.get_user(user_id)
    preferred_network = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    notifications_enabled = getattr(user_record, "notifications_enabled", True)
    main_kb = get_main_keyboard(
        preferred_network=preferred_network,
        notifications_enabled=notifications_enabled,
    )
    try:
        await query.edit_message_text(
            text="✅ <b>Settings saved.</b>\n\nYour digest will reflect your app selection.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _refresh_keyboard_after_preference_change(query, user_id: int) -> None:
    """Refresh reply_markup in-place after network/notification changes."""
    user_record = await db.get_user(user_id)
    if not user_record:
        return

    preferred_network = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    notifications_enabled = getattr(user_record, "notifications_enabled", True)

    message_text = getattr(getattr(query, "message", None), "text", "") or ""
    is_settings_root = message_text.startswith("⚙️ Celo GovAI Hub — Select Your Apps")
    is_wallet_menu = message_text.startswith("👛 Set Wallet")

    if is_settings_root:
        user_apps = await db.get_user_apps_by_category(user_id)
        reply_markup = get_settings_keyboard(
            user_apps,
            preferred_network=preferred_network,
            notifications_enabled=notifications_enabled,
        )
    elif is_wallet_menu:
        reply_markup = get_wallet_keyboard(preferred_network)
    else:
        reply_markup = get_main_keyboard(
            preferred_network=preferred_network,
            notifications_enabled=notifications_enabled,
        )

    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    except BadRequest:
        # Best-effort only — some Telegram message types may not support editing markup.
        pass


async def _handle_notifications_toggle(query, user_id: int) -> None:
    """Toggle vote alerts and update UI state."""
    new_value = await db.toggle_notifications_enabled(user_id)
    toast = "Notifications turned ON" if new_value else "Notifications turned OFF"
    await query.answer(toast, show_alert=False)
    await _refresh_keyboard_after_preference_change(query, user_id)

    logger.info("[SETTINGS] notifications_enabled toggled | user=%s | enabled=%s", user_id, new_value)


async def _handle_network_switch(query, user_id: int) -> None:
    """Toggle user's governance network between mainnet and alfajores.

    Reads the current preferred_network from the DB, flips it to the other
    value, persists the change, and refreshes the keyboard in-place so the
    button immediately reflects the new active network.
    """
    user_record = await db.get_user(user_id)
    current = getattr(user_record, "preferred_network", "mainnet") or "mainnet"
    new_network = "alfajores" if current.lower() == "mainnet" else "mainnet"

    await db.set_preferred_network(user_id, new_network)

    network_label = "🍪 Alfajores" if new_network == "alfajores" else "🟡 Mainnet"
    await query.answer(f"Network switched to {network_label}", show_alert=False)
    await _refresh_keyboard_after_preference_change(query, user_id)

    logger.info(
        "[SETTINGS] preferred_network toggled | user=%s | %s → %s",
        user_id,
        current,
        new_network,
    )


async def _handle_toggle_app(query, user_id: int) -> None:
    """Toggle one app and refresh the category submenu (not the root)."""
    app_name: str = query.data.split(":", 1)[1]

    cat_key = next(
        (cat for cat, apps in APPS_AVAILABLE.items() if app_name in apps),
        None,
    )
    if not cat_key:
        await query.answer("App not found.", show_alert=True)
        return

    user_apps = await db.get_user_apps_by_category(user_id)
    all_enabled = [a for apps in user_apps.values() for a in apps]
    currently_enabled = app_name in user_apps.get(cat_key, [])

    if currently_enabled and len(all_enabled) <= 1:
        await query.answer("Select at least one app.", show_alert=True)
        return

    await db.update_user_app(user_id, app_name, enabled=not currently_enabled)

    updated_apps = await db.get_user_apps_by_category(user_id)
    try:
        await query.edit_message_reply_markup(
            reply_markup=get_category_keyboard(cat_key, updated_apps),
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
            "⭐ You're already Premium!\n\n"
            "Enjoy unlimited AI asks with llama-3.3-70b-versatile.\n"
            "Use /status to check your expiration date."
        )
    else:
        text = PREMIUM_MESSAGE.format(BOT_WALLET=bot_wallet)
    try:
        await query.message.edit_text(
            text,
            reply_markup=get_premium_keyboard(),
        )
    except BadRequest:
        pass


async def _handle_premium_plan(query, user_id: int, days: int) -> None:
    """Show plan-specific instructions and wallet status after user selects 7d or 30d."""
    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")
    amount = PLAN_7D_CUSD if days == 7 else PLAN_30D_CUSD
    label = f"{days}-day Premium"
    user_wallet = await db.get_wallet(user_id)

    if user_wallet:
        wallet_line = (
            f"Your registered wallet:\n"
            f"{user_wallet}\n\n"
            f"Just send {amount:.2f} cUSD to the address below — "
            f"Premium activates automatically in ~60s after confirmation."
        )
    else:
        wallet_line = (
            f"No wallet registered yet.\n"
            f"Use /setwallet 0xYourWallet for automatic activation.\n\n"
            f"Or send {amount:.2f} cUSD and use /confirmpayment 0xTxHash."
        )

    try:
        await query.edit_message_text(
            f"{label} selected\n\n"
            f"Amount: {amount:.2f} cUSD\n\n"
            f"Send to:\n"
            f"{bot_wallet}\n\n"
            f"{wallet_line}\n\n"
            f"Paid via exchange? Use:\n"
            f"/confirmpayment 0xTxHash",
            reply_markup=get_premium_plan_keyboard(days),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_premium_confirm(query) -> None:
    """Instruct user to confirm payment with /confirmpayment and where to find tx hash."""
    try:
        await query.edit_message_text(
            "To confirm your payment, send the transaction hash:\n\n"
            "/confirmpayment 0xYourTxHash\n\n"
            "Find your tx hash at:\n"
            "https://celo.blockscout.com",
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_premium_back(query, user_id: int) -> None:
    """Return to the main premium plans screen."""
    bot_wallet = get_env_or_fail("BOT_WALLET_ADDRESS")
    user_wallet = await db.get_wallet(user_id)

    wallet_line = (
        f"Registered wallet: {user_wallet}"
        if user_wallet
        else "No wallet registered. Use /setwallet 0xYourWallet for auto-activation."
    )

    try:
        await query.edit_message_text(
            f"Premium plans — Celo GovAI Hub\n\n"
            f"7-day Premium  — {PLAN_7D_CUSD:.2f} cUSD\n"
            f"30-day Premium — {PLAN_30D_CUSD:.2f} cUSD\n\n"
            f"Send cUSD stablecoin to:\n"
            f"{bot_wallet}\n\n"
            f"Send from a personal wallet (MiniPay, Valora, MetaMask).\n"
            f"Exchanges use intermediate addresses and won't be detected.\n\n"
            f"After sending, tap the button below or use:\n"
            f"/confirmpayment [tx_hash]\n\n"
            f"{wallet_line}",
            reply_markup=get_premium_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ── resubscribe ────────────────────────────────────────────────────────────────

async def _handle_resubscribe(query, user_id: int) -> None:
    """Handle Re-subscribe button from unsubscribe flow (idempotent)."""
    from src.bot.handlers import _next_digest_str

    user = await db.get_user(user_id)
    if user and user.subscribed:
        try:
            await query.edit_message_text(
                "You are already subscribed!\n\n"
                f"Next digest: {_next_digest_str()}"
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
        return

    await db.update_subscription(user_id, True)
    logger.info("[RESUBSCRIBE] User re-subscribed | user=%s", user_id)

    try:
        await query.edit_message_text(
            "Welcome back! You are now re-subscribed.\n\n"
            f"Next digest: {_next_digest_str()}\n\n"
            "Use /settings to customize which apps you follow."
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ── help ───────────────────────────────────────────────────────────────────────

async def _handle_help_open(query, user_id: int) -> None:
    """Show help message inline."""
    logger.debug("[HELP] help:open | user=%d", user_id)
    help_kb = get_help_keyboard()

    try:
        await query.edit_message_text(
            HELP_MESSAGE_TEXT,
            reply_markup=help_kb,
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        await query.message.reply_text(
            HELP_MESSAGE_TEXT,
            reply_markup=help_kb,
            parse_mode=ParseMode.HTML,
        )


async def _handle_govlist(query) -> None:
    """Fetch and display active governance proposals (Queued + Dequeued)."""
    rpc_url = get_env_or_fail("CELO_RPC_URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    try:
        await query.edit_message_text(
            "⏳ Fetching active governance proposals from the Celo network...",
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

    result = await get_active_proposals_onchain(w3, str(GOVERNANCE_ADDRESS))
    queued = result.get("Queued", [])
    active = result.get("Active", [])

    queued_str = ", ".join(str(x) for x in queued) if queued else "None"
    active_str = ", ".join(str(x) for x in active) if active else "None"

    text = (
        "🏛️ <b>Celo Governance — Active Proposals</b>\n\n"
        f"⏳ <b>Queued:</b> {queued_str}\n"
        f"🗳️ <b>Active Voting:</b> {active_str}\n\n"
        "<i>Use <code>/proposal &lt;id&gt;</code> to read an AI summary and "
        "<code>/vote &lt;id&gt;</code> to cast your vote.</i>"
    )

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=governance_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_governance_open(query) -> None:
    """Show the Governance Hub menu."""
    try:
        await query.edit_message_text(
            text=GOVERNANCE_HUB_MESSAGE,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=get_governance_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_govhistory(query) -> None:
    """Fetch and display recent governance history proposal IDs."""
    rpc_url = get_env_or_fail("CELO_RPC_URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    try:
        await query.edit_message_text(
            "⏳ Fetching governance history from the Celo network...",
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

    proposal_ids = await get_historical_proposals_onchain(w3, str(GOVERNANCE_ADDRESS))
    ids_str = ", ".join(str(x) for x in proposal_ids) if proposal_ids else "None"

    text = (
        "📚 <b>Celo Governance — History</b>\n\n"
        "<i>Recent concluded proposals (Executed, Rejected, or Expired):</i>\n"
        f"{ids_str}\n\n"
        "<i>Use <code>/proposal &lt;id&gt;</code> for an AI summary.</i>"
    )

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=governance_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_govstatus(query, user_id: int) -> None:
    """Explain next steps for delegation status checks and delegation."""
    user_wallet = await db.get_wallet(user_id)
    wallet_line = (
        f"Registered wallet: <code>{user_wallet}</code>\n\n"
        if user_wallet
        else "No wallet registered yet.\n\nUse:\n<code>/setwallet 0xYourWalletAddress</code>\n\n"
    )

    text = (
        "🧾 <b>My Status & Delegate</b>\n\n"
        f"{wallet_line}"
        "Next steps:\n"
        "- Check status: <code>/govstatus</code>\n"
        "- Delegation guide: <code>/delegate</code>\n\n"
        "<i>Note: delegation is self-custodial. You never share private keys.</i>"
    )

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=governance_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ── export for app.py ──────────────────────────────────────────────────────────

callback_query_handler = CallbackQueryHandler(callback_router)
