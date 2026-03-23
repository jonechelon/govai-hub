# src/bot/callbacks.py
# GovAI Hub — central callback router for inline keyboards (P8)

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

from web3 import Web3

from src.bot.handlers import (
    HELP_MESSAGE_TEXT,
    ONCHAIN_HUB_MESSAGE,
    PLAN_30D_CUSD,
    PLAN_7D_CUSD,
    PREMIUM_MESSAGE,
    GOVERNANCE_HUB_MESSAGE,
    WELCOME_MESSAGE,
    build_earnings_dashboard_html,
    deliver_proposal_summary_from_anchor,
    format_governance_history_combined_html,
    format_govlist_proposals_html,
    format_vote_recorded_message,
    register_governance_vote_intent,
)
from src.bot.keyboards import (
    CATEGORY_DISPLAY,
    build_govlist_keyboard,
    get_category_keyboard,
    get_details_keyboard,
    get_digest_keyboard,
    get_earnings_dashboard_keyboard,
    get_links_keyboard,
    get_main_keyboard,
    get_onchain_hub_keyboard,
    get_onchain_txlist_keyboard,
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
from src.ai.groq_client import get_ai_suggestions
from src.ai.prompt_builder import parse_ai_suggestions
from src.utils.cache_manager import cache
from src.utils.blockscout_fetcher import (
    fetch_recent_txs,
    filter_defi_txs,
    filter_etherscan_txlist,
    filter_governance_txs,
    format_blockscout_message_html,
)
from src.utils.etherscan_v2 import fetch_address_txlist
from src.utils.onchain_txlist_format import (
    chain_id_for_network,
    format_txlist_message_html,
)
from src.utils.text_utils import hesc, truncate, truncate_wallet
from src.utils.env_validator import get_env_or_fail
from src.fetchers.governance_fetcher import (
    GOVERNANCE_ADDRESS,
    get_active_proposals_onchain,
)
from src.fetchers.coingecko_prices import (
    collect_symbols_from_trade_suggestions,
    enrich_stable_line_with_fiat,
    fetch_trade_token_market_stats,
    format_usd_price,
    is_stablecoin_symbol,
    normalize_trade_symbol,
    stablecoin_fiat_spot_fragment,
)
from src.fetchers.defillama_celo_tvl import (
    fetch_celo_chain_tvl_usd,
    format_tvl_usd,
)
from src.utils.celo_token_registry import resolve_swap_pair_from_suggestions
from src.utils.defi_links import (
    AI_QUICK_INTENTS,
    build_personalized_trading_dex_keyboard,
    build_venue_links,
)
from src.utils.onramp_links import build_onramp_keyboard_rows
from src.utils.digest_links import extract_links_from_digest, format_daily_sources_html
from src.utils.text_utils import hesc, truncate, truncate_wallet, proposal_header
from src.utils.user_network import (
    cycle_network,
    effective_user_network,
    network_toggle_label,
)
from src.scheduler.ai_trade_reminders import schedule_ai_trade_reminders_after_screen
from src.utils.url_fetch import fetch_url_text

logger = logging.getLogger(__name__)

# Varied fallback lines (emoji-led) when Groq returns nothing; count 2–5 per article URL.
_AI_TRADE_FALLBACK_POOL: list[str] = [
    "📈 Stake CELO for aligned validator yield",
    "🪙 Buy stCELO for liquid staking exposure",
    "💱 Swap CELO into stables on Mento",
    "🔁 Rotate CELO → USDC on Ubeswap",
    "🌱 Add CELO to a DEX pool strategy",
    "⚡ Route via aggregators for best execution",
    "🌍 Hedge with cEUR or USDm on Celo",
    "📊 Pair CELO with ecosystem tokens",
    "🛡️ Trim volatility — move to USDC",
    "🚀 Position for ecosystem upside",
]

# Prefixes when a Groq label has no leading emoji (keeps lines visually distinct).
_LINE_EMOJI_ROTATION: tuple[str, ...] = ("📈", "🪙", "💱", "🔁", "🌱")

_LEADING_EMOJI_RE = re.compile(
    r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E00-\U0001F1FFF]"
)


def _starts_with_visual_emoji(text: str) -> bool:
    """True if the string starts with a common emoji or symbol used as emoji."""
    if not text:
        return False
    return bool(_LEADING_EMOJI_RE.match(text))


def _format_ai_trade_label_line(index: int, label: str) -> str:
    """Ensure one emoji-led line; cap length for Telegram."""
    raw = (label or "").strip()
    if not raw:
        return _LINE_EMOJI_ROTATION[index % len(_LINE_EMOJI_ROTATION)] + " Explore on-chain options"
    if _starts_with_visual_emoji(raw):
        return raw[:72]
    prefix = _LINE_EMOJI_ROTATION[index % len(_LINE_EMOJI_ROTATION)]
    return f"{prefix} {raw}"[:72]


def _stable_fallback_line_count(article_url: str, digest_id: str) -> int:
    """Return 2–5 lines deterministically per article so the list length varies."""
    h = hashlib.sha256(f"{digest_id}|{article_url}".encode("utf-8")).hexdigest()
    return 2 + (int(h[:8], 16) % 4)


def _build_fallback_labels(article_url: str, digest_id: str) -> list[str]:
    """Pick a shuffled subset of varied fallback lines (stable seed per article)."""
    n = _stable_fallback_line_count(article_url, digest_id)
    seed = int(
        hashlib.sha256(f"{digest_id}|{article_url}|fallback".encode()).hexdigest()[:16],
        16,
    )
    pool = list(_AI_TRADE_FALLBACK_POOL)
    rng = random.Random(seed)
    rng.shuffle(pool)
    return [_format_ai_trade_label_line(i, line) for i, line in enumerate(pool[:n])]


def _build_ai_trade_display_labels(
    suggestions: list[dict],
    *,
    article_url: str,
    digest_id: str,
) -> list[str]:
    """Build 2–5 visible lines from Groq, or varied fallbacks; pad partial Groq with pool."""
    if not suggestions:
        return _build_fallback_labels(article_url, digest_id)

    out: list[str] = []
    for i, s in enumerate(suggestions[:5]):
        lbl = (s.get("label") or "").strip()
        if lbl:
            out.append(_format_ai_trade_label_line(i, lbl))
        else:
            seed = int(
                hashlib.sha256(
                    f"{digest_id}|{article_url}|empty{i}".encode(),
                ).hexdigest()[:16],
                16,
            )
            pick = _AI_TRADE_FALLBACK_POOL[seed % len(_AI_TRADE_FALLBACK_POOL)]
            out.append(_format_ai_trade_label_line(i, pick))

    # If Groq returned only 1 item, ensure at least 2 lines for balance
    if len(out) == 1:
        extra = _build_fallback_labels(article_url, f"{digest_id}:extra")
        if extra:
            out.append(extra[0])

    return out[:5]


def _groq_hint_for_trading_keyboard(suggestions: list[dict]) -> str:
    """Flatten venue, tokens, and labels so keyword scoring can align with Groq output."""
    if not suggestions:
        return ""
    parts: list[str] = []
    for s in suggestions[:5]:
        parts.append(
            f"{s.get('venue', '')} {s.get('token_in', '')} "
            f"{s.get('token_out', '')} {s.get('label', '')}"
        )
    return " ".join(parts)


def _classify_ai_trade_intent(suggestion: dict | None, label: str) -> str:
    """Classify digest AI Trade line: stake, swap, buy, sell, edge, or generic."""
    lab = (label or "").lower()
    ven = (suggestion.get("venue") or "").lower() if suggestion else ""

    if ven == "stcelo":
        return "stake"
    if "mistake" not in lab and "stake" in lab:
        return "stake"
    if "staking" in lab or "liquid st" in lab or "validator" in lab or "delegate" in lab:
        return "stake"

    if "edge" in lab or "arbitrage" in lab:
        return "edge"
    if any(k in lab for k in ("yield", "apy", "apr")):
        return "edge"

    if "sell" in lab:
        return "sell"
    if "buy" in lab:
        return "buy"
    if "swap" in lab or ven in ("mento", "ubeswap"):
        return "swap"
    return "generic"


def _stat_pair(
    stats: dict[str, dict[str, float | None]], sym: str
) -> tuple[float | None, float | None]:
    """Return (usd_price, usd_24h_change) for symbol."""
    row = stats.get(sym)
    if not row:
        return None, None
    u = row.get("usd")
    try:
        price = float(u) if u is not None else None
    except (TypeError, ValueError):
        price = None
    chg = row.get("usd_24h_change")
    try:
        chg_f = float(chg) if chg is not None else None
    except (TypeError, ValueError):
        chg_f = None
    return price, chg_f


def _fmt_24h_suffix(chg: float | None) -> str:
    if chg is None:
        return ""
    return f" ({chg:+.1f}% 24h)"


def _venue_display_name(venue: str) -> str:
    v = (venue or "").strip().lower()
    if v == "mento":
        return "Mento"
    if v == "ubeswap":
        return "Ubeswap"
    return v or "DEX"


def _fallback_symbols_in_label(label: str, stats: dict[str, dict]) -> set[str]:
    """Symbols mentioned in a fallback label that we have CoinGecko data for."""
    upper = (label or "").upper()
    found: set[str] = set()
    for sym in (
        "STCELO",
        "CELO",
        "USDC",
        "USDM",
        "CUSD",
        "CEUR",
        "BTC",
        "ETH",
        "USDT",
        "EURC",
        "SOL",
    ):
        if sym in upper and sym in stats:
            found.add(sym)
    return found


def _market_context_summary_html(
    stats: dict[str, dict[str, float | None]],
    spot_symbols: set[str],
    tvl_usd: float,
) -> str:
    """Italic summary: multi-asset spot + 24h (CoinGecko) and optional Celo TVL."""
    lines: list[str] = []
    if spot_symbols and stats:
        bits: list[str] = []
        for sym in sorted(spot_symbols):
            if sym == "STCELO" and "CELO" in spot_symbols:
                continue
            pu, chg = _stat_pair(stats, sym)
            if pu is None:
                continue
            row = stats.get(sym)
            if is_stablecoin_symbol(sym):
                multi = stablecoin_fiat_spot_fragment(row, sym)
                if multi:
                    frag = f"{sym} {multi}{_fmt_24h_suffix(chg)}"
                else:
                    frag = f"{sym} ${format_usd_price(pu)}{_fmt_24h_suffix(chg)}"
            else:
                frag = f"{sym} ${format_usd_price(pu)}{_fmt_24h_suffix(chg)}"
            bits.append(frag)
        if bits:
            lines.append(f"<i>Spot (CoinGecko): {hesc(' · '.join(bits[:8]))}</i>")
    if tvl_usd > 0:
        lines.append(
            f"<i>Celo on-chain TVL (DeFi Llama): {hesc(format_tvl_usd(tvl_usd))}</i>"
        )
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def _enrich_ai_trade_line_with_market_data(
    label: str,
    suggestion: dict | None,
    stats: dict[str, dict[str, float | None]],
    *,
    intent: str,
    tvl_usd: float,
) -> str:
    """Append TVL for staking; spot + 24h for swap/buy/sell; edge mix (English)."""
    base = (label or "").strip()
    if not base:
        return base

    tin = (
        normalize_trade_symbol(suggestion.get("token_in")) if suggestion else ""
    ) or "CELO"
    tout = normalize_trade_symbol(suggestion.get("token_out", "")) if suggestion else ""
    venue = str(suggestion.get("venue") or "").strip().lower() if suggestion else ""
    lbl_low = str(suggestion.get("label") or "").lower() if suggestion else ""
    vname = _venue_display_name(venue)

    # Staking: TVL only — no spot price on CELO/stCELO for this line
    if intent == "stake":
        if tvl_usd > 0:
            return (
                f"{base} — Celo DeFi TVL {format_tvl_usd(tvl_usd)} (DeFi Llama); "
                "complete staking in your wallet"
            )
        return f"{base} — Liquid staking on Celo; use the shortcuts below"

    # Edge / yield framing: volatility + ecosystem depth
    if intent == "edge":
        pu, chg = _stat_pair(stats, tin)
        tvl_bit = f"{format_tvl_usd(tvl_usd)} TVL" if tvl_usd > 0 else "on-chain liquidity"
        if chg is not None:
            return (
                f"{base} — {tin} 24h {chg:+.1f}% · {tvl_bit} (DeFi Llama); "
                "route via DEX shortcuts"
            )
        if pu is not None:
            return (
                f"{base} — {tin} ~${format_usd_price(pu)} · {tvl_bit}; "
                "size trades below"
            )
        if tvl_usd > 0:
            return f"{base} — Celo TVL {format_tvl_usd(tvl_usd)} (DeFi Llama); explore venues below"
        return base

    # Buy / Sell / Swap: require spot (and 24h when available)
    if intent not in ("buy", "sell", "swap", "generic"):
        return base

    if intent == "generic" and not stats:
        return base

    if suggestion is None:
        if intent == "stake":
            if tvl_usd > 0:
                return (
                    f"{base} — Celo DeFi TVL {format_tvl_usd(tvl_usd)} "
                    "(DeFi Llama); complete staking in your wallet"
                )
            return f"{base} — Liquid staking on Celo; use the shortcuts below"
        syms = _fallback_symbols_in_label(label, stats)
        if not syms:
            return base
        sym = sorted(syms, key=lambda s: (s != "CELO", s))[0]
        pu, chg = _stat_pair(stats, sym)
        if pu is None:
            return base
        if "sell" in label.lower():
            line = f"{base} — Sell {sym} near ~${format_usd_price(pu)}{_fmt_24h_suffix(chg)} USD"
            return enrich_stable_line_with_fiat(line, sym, stats)
        if "buy" in label.lower():
            line = f"{base} — Buy {sym} near ~${format_usd_price(pu)}{_fmt_24h_suffix(chg)} USD"
            return enrich_stable_line_with_fiat(line, sym, stats)
        if "swap" in label.lower():
            line = f"{base} — Swap {sym} near ~${format_usd_price(pu)}{_fmt_24h_suffix(chg)} USD"
            return enrich_stable_line_with_fiat(line, sym, stats)
        line = f"{base} — {sym} ~${format_usd_price(pu)}{_fmt_24h_suffix(chg)} (market ref)"
        return enrich_stable_line_with_fiat(line, sym, stats)

    pu, chg_in = _stat_pair(stats, tin)
    pout, chg_out = _stat_pair(stats, tout) if tout else (None, None)

    if intent == "buy":
        if pu is None:
            return base
        line = (
            f"{base} — Buy {tin} near ~${format_usd_price(pu)}"
            f"{_fmt_24h_suffix(chg_in)} USD"
        )
        return enrich_stable_line_with_fiat(line, tin, stats)

    if intent == "sell":
        if pu is None:
            return base
        line = (
            f"{base} — Sell {tin} near ~${format_usd_price(pu)}"
            f"{_fmt_24h_suffix(chg_in)} USD"
        )
        return enrich_stable_line_with_fiat(line, tin, stats)

    if intent == "swap":
        if pu is None:
            return base
        stables = frozenset({"USDM", "CUSD", "CEUR", "USDC", "USDT", "EURC"})
        if venue == "mento" and (tout in stables or "stable" in lbl_low):
            line = (
                f"{base} — Swap {tin} at ~${format_usd_price(pu)}"
                f"{_fmt_24h_suffix(chg_in)} into stables on Mento"
            )
            if tout and is_stablecoin_symbol(tout):
                return enrich_stable_line_with_fiat(line, tout, stats)
            if is_stablecoin_symbol(tin):
                return enrich_stable_line_with_fiat(line, tin, stats)
            return line
        if pout is not None and tout:
            line = (
                f"{base} — Swap {tin} ~${format_usd_price(pu)}"
                f"{_fmt_24h_suffix(chg_in)} vs {tout} ~${format_usd_price(pout)}"
                f"{_fmt_24h_suffix(chg_out)} on {vname}"
            )
            if is_stablecoin_symbol(tout):
                return enrich_stable_line_with_fiat(line, tout, stats)
            if is_stablecoin_symbol(tin):
                return enrich_stable_line_with_fiat(line, tin, stats)
            return line
        to_disp = tout or "another asset"
        line = (
            f"{base} — Swap {tin} at ~${format_usd_price(pu)}"
            f"{_fmt_24h_suffix(chg_in)} vs {to_disp} on {vname}"
        )
        if tout and is_stablecoin_symbol(tout):
            return enrich_stable_line_with_fiat(line, tout, stats)
        if is_stablecoin_symbol(tin):
            return enrich_stable_line_with_fiat(line, tin, stats)
        return line

    # generic
    if pu is None:
        pu, chg_in = _stat_pair(stats, "CELO")
    if pu is None:
        return base
    line = (
        f"{base} — {tin} ~${format_usd_price(pu)}"
        f"{_fmt_24h_suffix(chg_in)} (market reference)"
    )
    return enrich_stable_line_with_fiat(line, tin, stats)


async def _edit_ai_trade_result(
    query,
    link_digest_id: str,
    short_label: str,
    labels: list[str],
    *,
    article_title: str,
    article_body: str,
    groq_hint: str = "",
    suggestions: list[dict] | None = None,
    spot_reference_html: str = "",
    bot: Bot | None = None,
) -> None:
    """Render AI Trade lines + personalized on-chain Celo DEX keyboard + Back."""
    title_short = hesc(truncate(str(short_label), 60))
    suggestion_text = "\n".join(
        f"{i + 1}. <b>{hesc(lbl)}</b>" for i, lbl in enumerate(labels[:5])
    )
    trading_keyboard = build_personalized_trading_dex_keyboard(
        article_title,
        article_body,
        groq_hint,
        suggestions=suggestions,
    )
    rows = list(trading_keyboard.inline_keyboard)
    pair = resolve_swap_pair_from_suggestions(suggestions, groq_hint)
    wallet_address: str | None = None
    uid = query.from_user.id if query.from_user else None
    if uid is not None:
        try:
            wallet_address = await db.get_wallet(uid)
        except Exception:
            wallet_address = None
    rows.extend(
        build_onramp_keyboard_rows(
            suggestions,
            pair,
            wallet_address=wallet_address,
        )
    )
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data=f"back:{link_digest_id}",
            ),
        ],
    )
    combined_markup = InlineKeyboardMarkup(rows)
    try:
        await query.edit_message_text(
            f"💹 <b>AI Trade — {title_short}</b>\n\n"
            f"{spot_reference_html}"
            f"{suggestion_text}\n\n"
            "<b>On-chain Celo — trading shortcuts</b>\n\n"
            "<i>AI shortcuts open deep links — complete swaps in your wallet. "
            "The bot never holds your keys.</i>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=combined_markup,
        )
    except BadRequest:
        return

    # Option B: reminder clock starts only after the final AI Trade screen is shown.
    if bot is not None and query.message is not None:
        schedule_ai_trade_reminders_after_screen(bot, query.message.chat_id)


async def _notify_html_parse_error(query, callback_data: str, exc: BadRequest) -> bool:
    """If ``exc`` is an HTML parse error, log and notify user. Returns True if handled."""
    err = str(exc).lower()
    if "can't parse" not in err and "parse" not in err:
        return False
    logger.error("[HTML_ERROR] parse failed in %s: %s", callback_data, exc)
    try:
        await query.edit_message_text("⚠️ Display error. Please try again.")
    except BadRequest:
        try:
            await query.message.reply_text("⚠️ Display error. Please try again.")
        except Exception as exc:
            logger.error("[HTML_ERROR] Failed to send fallback error message: %s", exc)
    return True


async def _fallback_reply_if_cannot_edit(
    query,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool = False,
) -> None:
    """Last resort when the callback message cannot be edited (rare)."""
    try:
        await query.message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as exc:
        logger.error("[CALLBACK] Fallback reply failed: %s", exc)


def _payout_expires_at_passed(expires_at: object) -> bool:
    """Return True if a payout request is past ``expires_at`` (UTC).

    Accepts aware/naive ``datetime`` or ISO-like strings from the database.
    """
    now = datetime.now(timezone.utc)
    if expires_at is None:
        return False
    if isinstance(expires_at, datetime):
        dt = expires_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return now >= dt.astimezone(timezone.utc)
    raw = str(expires_at).strip()
    if not raw:
        return False
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        elif " " in raw and "T" not in raw:
            dt = datetime.fromisoformat(raw.replace(" ", "T", 1))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return now >= dt.astimezone(timezone.utc)
    except ValueError:
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        return raw < now_str


async def handle_payout_approve(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ``payout:approve:<payout_id>`` — quorum approvals for treasury payouts."""
    query = update.callback_query
    if query is None or query.message is None:
        return

    user_id = query.from_user.id if query.from_user else 0
    chat_id = query.message.chat_id
    data = query.data or ""

    parts = data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid request.", show_alert=True)
        return
    try:
        payout_id = int(parts[2])
    except ValueError:
        await query.answer("Invalid request.", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except BadRequest:
        await query.answer("Unable to verify permissions.", show_alert=True)
        return
    except Exception:  # noqa: BLE001
        logger.exception("[PAYOUT] get_chat_member failed | chat_id=%s user_id=%s", chat_id, user_id)
        await query.answer("Unable to verify permissions.", show_alert=True)
        return

    if member.status not in ("administrator", "creator"):
        await query.answer("Admins only.", show_alert=True)
        return

    request_row = await db.get_payout_request(payout_id)
    if not request_row:
        await query.answer("Request not found.", show_alert=True)
        return

    if request_row["status"] != "pending":
        await query.answer(
            f"Request is already {request_row['status']}.",
            show_alert=True,
        )
        return

    if _payout_expires_at_passed(request_row["expires_at"]):
        await db.set_payout_status(payout_id, "expired")
        await query.answer()
        try:
            await query.edit_message_text(
                "⏰ Expired.",
                parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            pass
        return

    approved = await db.add_payout_approval(payout_id, user_id)
    if not approved:
        await query.answer("Already approved by you.", show_alert=True)
        return

    request_row = await db.get_payout_request(payout_id)
    try:
        approvals = json.loads(request_row["approvals_json"])
    except json.JSONDecodeError:
        approvals = []
    if not isinstance(approvals, list):
        approvals = []

    quorum = int(os.getenv("TREASURY_QUORUM", "2"))
    token = request_row.get("token") or ""
    treasury = (os.getenv("TREASURY_ADDRESS", "") or "").strip()
    treasury_esc = hesc(treasury)

    await query.answer()

    if len(approvals) >= quorum:
        await db.set_payout_status(payout_id, "approved")

        buttons = [
            [InlineKeyboardButton("Open Valora", url="https://valoraapp.com")],
            [
                InlineKeyboardButton(
                    "View Treasury on Celoscan",
                    url=f"https://celoscan.io/address/{treasury_esc}",
                )
            ],
        ]
        if token == "USDm":
            buttons.append(
                [InlineKeyboardButton("Swap on Mento", url="https://app.mento.org")]
            )

        n_esc = hesc(str(len(approvals)))
        q_esc = hesc(str(quorum))
        approved_text = (
            f"✅ <b>Approved ({n_esc}/{q_esc})</b>\n\n"
            "Transfer must be executed manually.\n"
            "Open a venue below to complete the transfer."
        )
        try:
            await query.edit_message_text(
                text=approved_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except BadRequest:
            pass

        logger.info("[PAYOUT] approved id=%s approvers=%s", payout_id, approvals)
    else:
        new_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"✅ Approve ({len(approvals)}/{quorum})",
                    callback_data=f"payout:approve:{payout_id}",
                )
            ]
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=new_keyboard)
        except BadRequest:
            pass


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks. MUST call query.answer() first (2.E1)."""
    query = update.callback_query

    data: str = query.data or ""
    user_id: int = update.effective_user.id

    logger.info("[CALLBACK] %s from user %d", data, user_id)

    try:
        # ai_pick / ai_quick: answer() must be conditional (alerts on errors) — not global.
        if data.startswith("ai_pick:"):
            parts = data.split(":")
            if len(parts) != 3:
                await query.answer("Invalid selection.", show_alert=True)
                return

            _, session_id, index_str = parts

            try:
                idx = int(index_str)
            except ValueError:
                await query.answer("Invalid selection.", show_alert=True)
                return

            suggestions = await db.get_ai_session(session_id)
            if not suggestions or idx < 0 or idx >= len(suggestions):
                await query.answer(
                    "⏰ Session expired. Use /aitrade again.",
                    show_alert=True,
                )
                return

            suggestion = suggestions[idx]

            intent = {
                "action": "swap",
                "amount": 0,
                "token_in": suggestion["token_in"],
                "token_out": suggestion["token_out"],
                "venue": suggestion["venue"],
                "fee_currency": "CELO",
            }

            keyboard = build_venue_links(intent)

            text = (
                "🤖 <b>AI Trade Suggestion</b>\n\n"
                f"Selected: <b>{hesc(suggestion['label'])}</b>\n\n"
                "Open one of the venues below to complete the trade in your wallet.\n"
                "Sign in your wallet — this bot never holds your keys."
            )

            await query.answer()
            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                if not await _notify_html_parse_error(query, data, e):
                    pass
            return

        elif data.startswith("ai_quick:"):
            parts = data.split(":")
            if len(parts) != 3:
                await query.answer("Invalid quick action.", show_alert=True)
                return

            _, key, proposal_id_str = parts
            logger.debug(
                "[CALLBACK] ai_quick | key=%s | proposal_id=%s",
                key,
                proposal_id_str,
            )

            intent_template = AI_QUICK_INTENTS.get(key)
            if not intent_template:
                await query.answer("Unknown quick action.", show_alert=True)
                return

            intent = {
                "action": intent_template["action"],
                "amount": 0,
                "token_in": intent_template["token_in"],
                "token_out": intent_template["token_out"],
                "venue": intent_template["venue"],
                "fee_currency": "CELO",
            }

            keyboard = build_venue_links(intent)

            text = (
                "🤖 <b>Quick DeFi Action</b>\n\n"
                "Open one of the venues below to complete this action in your wallet.\n"
                "Sign in your wallet — this bot never holds your keys."
            )

            await query.answer()
            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                if not await _notify_html_parse_error(query, data, e):
                    pass
            return

        elif data.startswith("autotrade:cancel:"):
            parts = data.split(":")
            if len(parts) != 3:
                await query.answer("Invalid request.", show_alert=True)
                return

            try:
                trade_id = int(parts[2])
            except ValueError:
                await query.answer("Invalid trade ID.", show_alert=True)
                return

            cancel_user_id = query.from_user.id if query.from_user else 0
            cancelled = await db.cancel_auto_trade(trade_id=trade_id, user_id=cancel_user_id)

            if not cancelled:
                await query.answer(
                    "Trade not found or already cancelled.",
                    show_alert=True,
                )
                return

            await query.answer("✅ Auto-trade cancelled.")

            try:
                await _render_my_status_screen(query, cancel_user_id)
            except BadRequest:
                pass
            return

        elif data.startswith("autotrade:create:"):
            # Format: autotrade:create:<proposal_id>
            parts = data.split(":")
            if len(parts) != 3:
                await query.answer("Invalid request.", show_alert=True)
                return

            try:
                proposal_id = int(parts[2])
            except ValueError:
                await query.answer("Invalid proposal ID.", show_alert=True)
                return

            create_user_id = query.from_user.id if query.from_user else 0
            user = await db.get_user(create_user_id)

            wallet = None
            if user:
                wallet = getattr(user, "user_wallet", None) or getattr(
                    user, "wallet_address", None
                )
            if not wallet:
                await query.answer()
                try:
                    await query.edit_message_text(
                        "💼 <b>Wallet not registered</b>\n\n"
                        "<i>Send your EVM address (0x…) to register and use DeFi features.</i>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_governance_keyboard(
                            back_callback="governance_menu",
                        ),
                    )
                except BadRequest:
                    pass
                return

            existing = await db.get_user_trade_for_proposal(
                user_id=create_user_id,
                proposal_id=proposal_id,
            )
            if existing:
                await query.answer(
                    "Already registered for this proposal.",
                    show_alert=True,
                )
                return

            # Strategy A: fixed stCELO stake intent (deterministic, no Groq round-trip).
            # For free-form intent, user can use /aitrade instead.
            intent = {
                "action": "stake",
                "amount": 0,
                "token_in": "CELO",
                "token_out": "stCELO",
                "venue": "stcelo",
                "fee_currency": "CELO",
            }

            trade_id = await db.save_auto_trade(
                user_id=create_user_id,
                proposal_id=proposal_id,
                intent_json=intent,
            )

            await query.answer("✅ Auto-trade registered!")

            text = (
                "🤖 <b>Auto-Trade Registered</b>\n\n"
                f"<b>Proposal:</b> #{proposal_id}\n"
                f"<b>Intent:</b> Stake CELO → stCELO via stCELO contract\n\n"
                "When this proposal is <b>executed on-chain</b>, you will receive "
                "a notification with direct links to complete the trade in your wallet.\n\n"
                "<i>Sign in your wallet — this bot never holds your keys.</i>\n\n"
                f"<b>Trade ID:</b> <code>{trade_id}</code>\n"
                "You can cancel anytime from 🏛️ Governance → My Status."
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "👤 My Status",
                    callback_data="gov:status",
                )],
                [InlineKeyboardButton(
                    "⬅️ Back to Proposal",
                    callback_data=f"gov:voteview:{proposal_id}",
                )],
            ])

            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                if not await _notify_html_parse_error(query, data, e):
                    pass
            return

        elif data.startswith("payout:approve:"):
            await handle_payout_approve(update, context)
            return

        if data.startswith("digest:link:"):
            await _handle_digest_link(query, context, user_id, data)
            return

        if data == "noop":
            await query.answer(
                "Use the buttons below for swap or staking shortcuts. "
                "Tap stCELO, USDm, or the stake option, then complete the action "
                "in your wallet — the bot never holds your keys.",
                show_alert=True,
            )
            return

        if data == "noop:aitrade:header":
            await query.answer(
                "Tap a numbered button below (1, 2, 3…) for the digest item you want. "
                "Each opens AI Trade shortcuts matched to that story — then complete any "
                "swap in your wallet. The bot never holds your keys.",
                show_alert=True,
            )
            return

        if data.startswith("gov:share:"):
            # [🔗 Share & Earn] on proposal view — new message only (never edit proposal card).
            parts = data.split(":")
            try:
                pid = int(parts[2])
            except (IndexError, ValueError):
                await query.answer("Invalid proposal.", show_alert=True)
                return

            bot_username = os.getenv("BOT_USERNAME", "GovAIHub_bot").lstrip("@")
            if not bot_username.strip():
                logger.error(
                    "[SHARE_ERROR] BOT_USERNAME is empty — cannot generate share link"
                )
                await query.answer(
                    "Share link unavailable. Set BOT_USERNAME in environment.",
                    show_alert=True,
                )
                return

            await query.answer()

            share_link = (
                f"https://t.me/{bot_username}?start=proposal_{pid}_ref_{user_id}"
            )
            encoded = urllib.parse.quote(share_link, safe="")

            stats = await db.get_referral_stats(user_id)
            gov_points = stats.get("gov_points", 0)
            referral_count = stats.get("referral_count", 0)

            user = await db.get_user(user_id)
            wallet = None
            if user:
                wallet = getattr(user, "user_wallet", None) or getattr(
                    user, "wallet_address", None
                )
            wallet_warning = (
                "\n⚠️ <i>Register your wallet with /start to receive USDm rewards.</i>\n"
                if not wallet
                else ""
            )

            text = (
                f"🔗 <b>Share Proposal #{hesc(str(pid))} — Earn Rewards</b>\n\n"
                f"<i>Invite others to vote on Celo governance.\n"
                f"Earn GovPoints and USDm when they act.</i>\n"
                f"{wallet_warning}\n"
                f"📎 Your referral link:\n"
                f"<code>{hesc(share_link)}</code>\n\n"
                f"⭐ Your GovPoints: <b>{gov_points}</b>\n"
                f"👥 Referrals: <b>{referral_count}</b> voters brought\n\n"
                f"<i>USDm rewards distributed weekly via\n"
                f"DAO Treasury to your registered wallet.</i>"
            )

            share_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 Share on Telegram",
                        url=(
                            "https://t.me/share/url"
                            f"?url={encoded}"
                            f"&text=Vote+on+Celo+Governance+%23{pid}+via+GovAI+Hub"
                        ),
                    )
                ]
            ])

            try:
                await query.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=share_keyboard,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                if not await _notify_html_parse_error(query, data, e):
                    pass
            return

        await query.answer()  # MUST be first for other callbacks — stops loading spinner
        if data == "start":
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
        elif data == "menu:earnings":
            text = await build_earnings_dashboard_html(user_id)
            try:
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=get_earnings_dashboard_keyboard(),
                )
            except BadRequest:
                pass
        elif data == "menu:onchain_hub":
            await _handle_onchain_hub(query)
        elif data.startswith("onchain:txlist:"):
            parts = data.split(":", 2)
            scope = parts[2] if len(parts) > 2 else "all"
            await _handle_onchain_txlist(query, user_id, scope)
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
        elif query.data == "gov:list":
            try:
                await _handle_govlist(query)
            except BadRequest:
                pass
        elif query.data == "gov:history":
            try:
                await _handle_govhistory(query)
            except BadRequest:
                pass
        elif query.data == "gov:back":
            try:
                await query.edit_message_text(
                    "<b>🏛 GovAI Hub — Governance</b>\n\n"
                    "Select an option below:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_governance_keyboard(),
                )
            except BadRequest:
                pass
        elif query.data == "gov:status":
            try:
                await _render_my_status_screen(query, user_id)
            except BadRequest:
                pass
        elif data.startswith("gov:voteview:"):
            # query.answer() already ran above (§10) before this branch.
            try:
                proposal_id = int(data.split(":", 2)[2])
            except (IndexError, ValueError):
                try:
                    await query.edit_message_text(
                        "❌ Invalid proposal ID. Open Active Proposals and try again.",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=get_governance_keyboard(
                            back_callback="governance_menu",
                        ),
                    )
                except BadRequest:
                    pass
            else:
                await deliver_proposal_summary_from_anchor(
                    query.message,
                    proposal_id,
                    edit_same_message=True,
                )
        elif data.startswith("vote:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                try:
                    await query.edit_message_text(
                        "❌ Invalid vote action. Please open the proposal again.",
                        reply_markup=get_governance_keyboard(
                            back_callback="governance_menu",
                        ),
                    )
                except BadRequest:
                    pass
            else:
                _, choice_raw, prop_raw = parts
                try:
                    proposal_id = int(prop_raw)
                except ValueError:
                    try:
                        await query.edit_message_text(
                            "❌ Invalid proposal ID.",
                            reply_markup=get_governance_keyboard(
                                back_callback="governance_menu",
                            ),
                        )
                    except BadRequest:
                        pass
                else:
                    err = await register_governance_vote_intent(
                        user_id, proposal_id, choice_raw
                    )
                    if err:
                        try:
                            await query.edit_message_text(
                                err,
                                reply_markup=get_governance_keyboard(
                                    back_callback="governance_menu",
                                ),
                            )
                        except BadRequest:
                            pass
                    else:
                        choice = choice_raw.strip().upper()
                        try:
                            await query.edit_message_text(
                                format_vote_recorded_message(proposal_id, choice),
                                reply_markup=get_governance_keyboard(
                                    back_callback="governance_menu",
                                ),
                            )
                        except BadRequest:
                            pass
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
        except Exception as final_exc:  # noqa: BLE001
            # Best-effort: avoid raising inside the error handler itself.
            logger.debug("[CALLBACK] Final emergency answer failed: %s", final_exc)


async def _handle_onchain_hub(query) -> None:
    """Show the on-chain activity hub (Governance / AI Trade → tx history, not governance:open).

    ``query.answer()`` is invoked in the callback router before this runs (§10).
    """
    try:
        await query.edit_message_text(
            text=ONCHAIN_HUB_MESSAGE,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=get_onchain_hub_keyboard(),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_onchain_txlist(query, user_id: int, scope: str) -> None:
    """Show recent transactions (Etherscan V2 when configured; else Blockscout).

    ``query.answer()`` is invoked in the callback router before this runs (§10).
    """
    if scope not in {"all", "governance", "aitrade"}:
        scope = "all"

    empty_gov_html = (
        "🏛️ <b>No governance transactions found.</b>\n\n"
        "<i>This wallet has not interacted with the Celo Governance contract yet.\n"
        "Governance votes are made directly via the Governance hub above.</i>"
    )
    empty_defi_html = (
        "💱 <b>No DeFi trade transactions found.</b>\n\n"
        "<i>This wallet has no recent swaps or staking activity on Celo.\n"
        "Use the AI shortcuts below to open a trade in your wallet.</i>"
    )
    empty_all_html = (
        "📭 <b>No recent on-chain activity.</b>\n\n"
        "<i>This wallet has no transactions in the last fetch window.</i>"
    )

    user = await db.get_user(user_id)
    preferred_network = effective_user_network(user) if user else "mainnet"
    wallet: str | None = None
    if user:
        wallet = (
            getattr(user, "wallet_address", None)
            or getattr(user, "user_wallet", None)
        )

    if not wallet:
        try:
            await query.edit_message_text(
                "💼 <b>Wallet not registered</b>\n\n"
                "Send your EVM address (0x…).",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=get_onchain_txlist_keyboard(
                    wallet=wallet,
                    network=preferred_network,
                ),
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
        return

    try:
        await query.edit_message_text(
            "⏳ <b>Loading transaction history…</b>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

    chain_id = chain_id_for_network(preferred_network)
    raw_result = await fetch_address_txlist(
        wallet,
        offset=15,
        chain_id=chain_id,
    )

    if scope == "all":
        header = "📜 <b>Recent transactions — On-chain activity</b>"
    elif scope == "governance":
        header = "🏛️ <b>Recent transactions — Governance</b>"
    else:
        header = "💹 <b>Recent transactions — AI Trade</b>"

    if raw_result is not None:
        txs: list[dict] = raw_result if isinstance(raw_result, list) else []
        txs = filter_etherscan_txlist(txs, scope)
        empty_etherscan: str | None = None
        if not txs:
            if scope == "governance":
                empty_etherscan = empty_gov_html
            elif scope == "aitrade":
                empty_etherscan = empty_defi_html
            elif scope == "all":
                empty_etherscan = empty_all_html
        body = format_txlist_message_html(
            header_line=header,
            address=wallet,
            txs=txs,
            network=preferred_network,
            max_rows=8,
            empty_message_html=empty_etherscan,
        )
    else:
        logger.info("[TX] Etherscan unavailable — falling back to Blockscout")
        tx_data = await fetch_recent_txs(
            wallet,
            limit=20,
            network=preferred_network,
        )
        empty_blockscout: str | None = None
        if scope == "governance":
            gov_only = filter_governance_txs(tx_data)
            tx_data = {"native": gov_only, "tokens": []}
            if not gov_only:
                empty_blockscout = empty_gov_html
        elif scope == "aitrade":
            defi_only = filter_defi_txs(tx_data)
            tx_data = {"native": [], "tokens": defi_only}
            if not defi_only:
                empty_blockscout = empty_defi_html
        elif scope == "all":
            if not tx_data.get("native") and not tx_data.get("tokens"):
                empty_blockscout = empty_all_html
        body = format_blockscout_message_html(
            header_line=header,
            address=wallet,
            tx_data=tx_data,
            network=preferred_network,
            max_rows=8,
            empty_message_html=empty_blockscout,
        )
    if scope == "aitrade":
        body += (
            "\n\n<i>AI shortcuts open deep links — complete swaps in your wallet. "
            "The bot never holds your keys.</i>"
        )
    body = truncate(body, 3800)

    try:
        await query.edit_message_text(
            text=body,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=get_onchain_txlist_keyboard(
                wallet=wallet,
                network=preferred_network,
            ),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _handle_start(query, user_id: int) -> None:
    """Return to the main menu screen."""
    user_record = await db.get_user(user_id)
    preferred_network = (
        effective_user_network(user_record) if user_record else "mainnet"
    )
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
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        await _fallback_reply_if_cannot_edit(
            query,
            WELCOME_MESSAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb,
            disable_web_page_preview=True,
        )


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
    preferred_network = (
        effective_user_network(user_record) if user_record else "mainnet"
    )
    wallet_kb = get_wallet_keyboard(preferred_network)

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=wallet_kb,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        await _fallback_reply_if_cannot_edit(
            query,
            text,
            reply_markup=wallet_kb,
        )


# ── digest ────────────────────────────────────────────────────────────────────

async def _handle_digest_latest(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Generate digest data and show the AI Trade sources view (main menu button).

    Uses the same DigestGenerator pipeline as /digest for cache + sections, but
    displays a numbered source list instead of the long Groq summary.
    """
    callback_data = query.data or ""
    logger.info("[CALLBACK] _handle_digest_latest | data=%s | user=%d", callback_data, user_id)

    try:
        await query.edit_message_text(
            "⏳ <i>Generating your Celo digest…</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except BadRequest:
        pass

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
            await query.edit_message_text("❌ Failed to load digest. Try again.")
        except BadRequest:
            pass
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
            await query.edit_message_text("❌ Failed to load digest. Try again.")
        except BadRequest:
            pass
        return

    digest_id = result.get("digest_id")

    if not digest_id:
        logger.error(
            "[DIGEST_CALLBACK] Invalid digest result for user=%d | keys=%s",
            user_id,
            list(result.keys()),
        )
        try:
            await query.edit_message_text("❌ Failed to load digest. Try again.")
        except BadRequest:
            pass
        return

    if not result.get("text"):
        logger.warning(
            "[DIGEST_CALLBACK] Empty Groq text | user=%d | digest_id=%s",
            user_id,
            digest_id,
        )

    links = await extract_links_from_digest(digest_id)
    text = format_daily_sources_html(links)
    link_count = len(links)

    if len(text) > 4096:
        text = truncate(text, 4090)

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_digest_keyboard(digest_id, link_count),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        logger.warning(
            "[DIGEST_CALLBACK] BadRequest editing message for user=%d | error=%s",
            user_id,
            exc,
        )
        try:
            await query.edit_message_text(
                text=truncate(text, 4000),
                reply_markup=get_digest_keyboard(digest_id, link_count),
                disable_web_page_preview=True,
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.error(
                "[DIGEST_CALLBACK] Fallback edit failed for user=%d | error=%s",
                user_id,
                inner_exc,
                exc_info=True,
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[DIGEST_CALLBACK] Unexpected error editing message for user=%d | error=%s",
            user_id,
            exc,
            exc_info=True,
        )
        try:
            await query.edit_message_text("❌ Failed to load digest. Try again.")
        except BadRequest:
            pass
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


async def _handle_digest_link(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    data: str,
) -> None:
    """Fetch article text, run AI Trade suggestions (Groq), show venue deep links."""
    _ = context, user_id
    parts = data.split(":", 3)
    if len(parts) != 4:
        await query.answer("Invalid link.", show_alert=True)
        return

    _, _, link_digest_id, raw_idx = parts
    try:
        idx = int(raw_idx)
    except ValueError:
        await query.answer("Invalid link.", show_alert=True)
        return

    links = await extract_links_from_digest(link_digest_id)
    if idx < 1 or idx > len(links):
        await query.answer("Link not available.", show_alert=True)
        return

    item = links[idx - 1]
    url = item.get("url") or item.get("link", "")
    full_title = item.get("title", f"Link {idx}")
    short_label = item.get("display_title") or full_title

    await query.answer()

    try:
        await query.edit_message_text(
            f"🔍 <b>Reading article…</b>\n\n<i>{hesc(str(short_label))}</i>",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest:
        pass

    scraped = await fetch_url_text(url)
    scrape_ok = bool((scraped or "").strip())

    suggestions: list[dict] = []
    if scrape_ok:
        user_blob = f"Article: {full_title}\n\n{scraped}"
        if len(user_blob) > 14000:
            user_blob = user_blob[:14000]
        try:
            raw = await get_ai_suggestions(user_blob)
            suggestions = parse_ai_suggestions(raw)
        except Exception as exc:
            logger.warning("[AI_TRADE] Groq failed for %s: %s", url, exc)
            suggestions = []

    if not suggestions:
        logger.info(
            "[AI_TRADE] Using fallback label set (scrape_ok=%s) | url=%s",
            scrape_ok,
            url,
        )

    labels = _build_ai_trade_display_labels(
        suggestions,
        article_url=url,
        digest_id=link_digest_id,
    )

    sym_set = collect_symbols_from_trade_suggestions(suggestions)
    if not sym_set:
        sym_set = {"CELO"}
    stats = await fetch_trade_token_market_stats(sym_set)

    intents = [
        _classify_ai_trade_intent(
            suggestions[i] if i < len(suggestions) else None,
            labels[i],
        )
        for i in range(len(labels))
    ]
    need_tvl = any(x in ("stake", "edge") for x in intents)
    tvl_usd = await fetch_celo_chain_tvl_usd() if need_tvl else 0.0

    spot_syms: set[str] = set()
    for i, intent in enumerate(intents):
        sug = suggestions[i] if i < len(suggestions) else None
        if intent not in ("buy", "sell", "swap", "edge", "generic"):
            continue
        if sug:
            for key in ("token_in", "token_out"):
                sym = normalize_trade_symbol(sug.get(key))
                if sym and sym in stats:
                    spot_syms.add(sym)
        else:
            spot_syms |= _fallback_symbols_in_label(labels[i], stats)

    has_price_intent = any(
        x in ("buy", "sell", "swap", "edge", "generic") for x in intents
    )
    if has_price_intent and not spot_syms and "CELO" in stats:
        spot_syms.add("CELO")

    enriched_labels: list[str] = []
    for i, lbl in enumerate(labels):
        sug: dict | None = suggestions[i] if i < len(suggestions) else None
        line = _enrich_ai_trade_line_with_market_data(
            lbl,
            sug,
            stats,
            intent=intents[i],
            tvl_usd=tvl_usd,
        )
        if len(line) > 240:
            line = line[:237] + "…"
        enriched_labels.append(line)

    spot_reference_html = _market_context_summary_html(stats, spot_syms, tvl_usd)

    groq_hint = _groq_hint_for_trading_keyboard(suggestions)

    await _edit_ai_trade_result(
        query,
        link_digest_id,
        short_label,
        enriched_labels,
        article_title=full_title,
        article_body=scraped if scrape_ok else "",
        groq_hint=groq_hint,
        suggestions=suggestions,
        spot_reference_html=spot_reference_html,
        bot=context.bot,
    )


async def _handle_links(
    query, context: ContextTypes.DEFAULT_TYPE, digest_id: str
) -> None:
    """
    Handle links:{digest_id} callback.
    Extracts URLs from cached digest and displays a numbered list.
    """
    user_id = query.from_user.id if query.from_user else 0
    links = await extract_links_from_digest(digest_id)

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
        title = truncate(link["title"], 60)
        source = link["source"]
        url = link["url"]
        lines.append(f"{i}. {title}\n   {source} — {url}\n")

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        full_text = truncate(full_text, 3950)

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

    links = await extract_links_from_digest(digest_id)
    if links:
        text = format_daily_sources_html(links)
        link_count = len(links)
    else:
        text = payload.get("text", "")
        link_count = 0
        if not text:
            await query.answer("❌ Could not reload digest.", show_alert=True)
            return

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_digest_keyboard(digest_id, link_count),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
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
    ask_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data=f"back:{digest_id}")],
    ])
    try:
        await query.edit_message_text(
            text=msg,
            reply_markup=ask_kb,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ── settings ───────────────────────────────────────────────────────────────────

async def _handle_settings_open(
    query, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> None:
    """Open root settings menu with 4 category buttons."""
    user_record = await db.get_user(user_id)
    preferred_network = (
        effective_user_network(user_record) if user_record else "mainnet"
    )
    notifications_enabled = getattr(user_record, "notifications_enabled", True)
    user_apps = await db.get_user_apps_by_category(user_id)
    # Settings header with context line per §3 template
    settings_text = (
        "<b>⚙️ Settings</b>\n\n"
        "<i>Manage your alerts, network, and wallet preferences.</i>\n\n"
        "Tap a category to manage its apps.\n"
        "✅ = all enabled  ☑️ = some enabled  ☐ = none"
    )
    try:
        await query.message.edit_text(
            settings_text,
            parse_mode=ParseMode.HTML,
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
    preferred_network = (
        effective_user_network(user_record) if user_record else "mainnet"
    )
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


async def _refresh_keyboard_after_preference_change(
    query,
    user_id: int,
    *,
    preferred_network_override: str | None = None,
) -> None:
    """Refresh reply_markup in-place after network/notification changes.

    Args:
        query: Callback query whose message markup should be updated.
        user_id: Telegram user id.
        preferred_network_override: If set, use this for network-dependent buttons
            instead of re-reading from DB (avoids stale session after a network switch).
    """
    user_record = await db.get_user(user_id)
    if not user_record:
        return

    preferred_network = (
        preferred_network_override
        if preferred_network_override is not None
        else effective_user_network(user_record)
    )
    notifications_enabled = getattr(user_record, "notifications_enabled", True)

    message_text = getattr(getattr(query, "message", None), "text", "") or ""
    # message.text strips HTML tags — match plain text, not raw HTML
    is_settings_root = "⚙️ Settings" in message_text
    is_wallet_menu = "👛 Set Wallet" in message_text or "💼 Set Wallet" in message_text

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
    """Cycle the user's chain network: mainnet → alfajores → sepolia → mainnet.

    Persists via :meth:`DatabaseManager.set_chain_network` and refreshes the
    inline keyboard in-place (main menu, Settings, or Set Wallet).
    """
    user_record = await db.get_user(user_id)
    if not user_record:
        await query.answer("Please use /start first.", show_alert=False)
        return

    current = effective_user_network(user_record)
    new_network = cycle_network(current)

    await db.set_chain_network(user_id, new_network)

    label = network_toggle_label(new_network)
    await query.answer(f"Network: {label}", show_alert=False)
    await _refresh_keyboard_after_preference_change(
        query, user_id, preferred_network_override=new_network
    )

    logger.info(
        "[SETTINGS] chain network cycled | user=%s | %s → %s",
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
            parse_mode=ParseMode.HTML,
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
            f"Premium plans — GovAI Hub\n\n"
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
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        await _fallback_reply_if_cannot_edit(
            query,
            HELP_MESSAGE_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=help_kb,
            disable_web_page_preview=True,
        )


async def _handle_govlist(query) -> None:
    """Fetch and display active governance proposals (Queued + Dequeued)."""
    rpc_url = get_env_or_fail("CELO_RPC_URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    try:
        await query.edit_message_text(
            "⏳ <b>Fetching active proposals…</b>",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

    try:
        result = await get_active_proposals_onchain(w3, str(GOVERNANCE_ADDRESS))
    except Exception as e:
        logger.warning("[RPC_ERROR] on-chain call failed: %s", e)
        try:
            await query.edit_message_text(
                "⚠️ <b>On-chain data unavailable</b>\n\n"
                "<i>RPC node is unreachable. Please try again in a moment.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_governance_keyboard(
                    back_callback="governance_menu",
                ),
            )
        except BadRequest as be:
            if not await _notify_html_parse_error(query, query.data or "gov:list", be):
                pass
        return

    queued = result.get("Queued", [])
    active = result.get("Active", [])

    if not queued and not active:
        try:
            await query.edit_message_text(
                "⚠️ <b>No active proposals</b>\n\n"
                "Check back later or use History to review past votes.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_governance_keyboard(
                    back_callback="governance_menu",
                ),
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise
        return

    text = format_govlist_proposals_html(queued, active)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_govlist_keyboard(queued, active),
            disable_web_page_preview=True,
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
    """Governance history: optional user votes (DB) + concluded proposal IDs (on-chain).

    ``query.answer()`` is invoked in the callback router before this runs (§10).
    """
    user_id = query.from_user.id if query.from_user else 0

    try:
        await query.edit_message_text(
            "⏳ <b>Fetching governance history…</b>",
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

    history_text = await format_governance_history_combined_html(user_id)

    try:
        await query.edit_message_text(
            text=history_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=get_governance_keyboard(
                back_callback="governance_menu",
            ),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _render_my_status_screen(query, user_id: int) -> None:
    """Show GovAI Hub — My Status: wallet, pending auto-trades, cancel buttons."""
    user = await db.get_user(user_id)
    wallet = (
        (getattr(user, "user_wallet", None) or getattr(user, "wallet_address", None))
        if user
        else None
    )

    # Empty state when wallet not registered (§5 of ui_protection.mdc)
    if not user or not wallet:
        try:
            await query.edit_message_text(
                "💼 <b>Wallet not registered</b>\n\nSend your EVM address (0x…).",
                parse_mode=ParseMode.HTML,
                reply_markup=get_governance_keyboard(
                    back_callback="governance_menu",
                ),
            )
        except BadRequest as e:
            if not await _notify_html_parse_error(query, query.data or "gov:status", e):
                pass
        return

    trades = await db.get_user_pending_trades(user_id)

    if trades:
        trades_lines = []
        for t in trades:
            try:
                intent = json.loads(t["intent_json"])
                action = intent.get("action", "?")
                token_in = intent.get("token_in", "?")
                token_out = intent.get("token_out", "?")
                venue = intent.get("venue", "?")
                label = f"{action.capitalize()} {token_in} → {token_out} via {venue}"
            except (json.JSONDecodeError, TypeError):
                label = "Unknown trade"
            trades_lines.append(
                f"-  {hesc(label)} "
                f"<i>(proposal #{t['proposal_id']})</i>"
            )
        trades_section = "<b>⏳ Pending Auto-Trades:</b>\n" + "\n".join(trades_lines)
    else:
        trades_section = "<b>⏳ Pending Auto-Trades:</b> <i>None</i>"

    # My Status — wallet + auto-trades only, no plan/tier field
    text = (
        "<b>👤 GovAI Hub — My Status</b>\n\n"
        f"💼 Wallet: <code>{hesc(truncate_wallet(wallet))}</code>\n\n"
        f"{trades_section}"
    )

    buttons = []
    for t in trades:
        try:
            intent = json.loads(t["intent_json"])
            token_out = intent.get("token_out", "?")
        except (json.JSONDecodeError, TypeError):
            token_out = "?"
        buttons.append([
            InlineKeyboardButton(
                f"❌ Cancel: {token_out} (proposal #{t['proposal_id']})",
                callback_data=f"autotrade:cancel:{t['id']}",
            )
        ])

    buttons.append(
        [InlineKeyboardButton("⬅️ Back", callback_data="governance_menu")]
    )
    keyboard = InlineKeyboardMarkup(buttons)

    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


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
            reply_markup=get_governance_keyboard(
                back_callback="governance_menu",
            ),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ── export for app.py ──────────────────────────────────────────────────────────

callback_query_handler = CallbackQueryHandler(callback_router)
