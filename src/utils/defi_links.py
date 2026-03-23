# src/utils/defi_links.py
# DeFi venue deep links for wallet-signed swaps (no server-side signing).

from __future__ import annotations

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from web3 import Web3

from src.utils.celo_token_registry import (
    CELO_MAINNET_TOKENS,
    address_for_symbol,
    resolve_swap_pair_from_suggestions,
)

logger = logging.getLogger(__name__)

# Token addresses on Celo Mainnet (see ``celo_token_registry.py`` for sources).
# stCELO: ERC-20 token (0xC668583…) — NOT the StakedCelo Account contract (0x4aAD…).
TOKEN_ADDRESSES: dict[str, str] = CELO_MAINNET_TOKENS

# Base URLs for each DeFi venue
_UBESWAP_BASE = "https://app.ubeswap.org/#/swap"
_MENTO_URL = "https://app.mento.org"
_VALORA_URL = "https://valoraapp.com"
_STCELO_CELOSCAN = (
    "https://celoscan.io/address/0xC668583dcbDc9ae6FA3CE46462758188adfdfC24"
)

# Backwards-compatible exports for callbacks.py (gov:stake convert keyboard).
STCELO_TOKEN_CELOSCAN_URL = _STCELO_CELOSCAN

# StakedCelo Account (pool) — informational only; swaps use TOKEN_ADDRESSES["stCELO"].
STCELO_ACCOUNT_CELOSCAN_URL = (
    "https://celoscan.io/address/0x4aAD04D41FD7fd495503731C5a2579e19054C432"
)

# Static map for ai_quick:* callbacks — no Groq (roadmap P3).
AI_QUICK_INTENTS: dict[str, dict[str, str]] = {
    "ubescelo": {
        "action": "swap",
        "token_in": "CELO",
        "token_out": "stCELO",
        "venue": "ubeswap",
    },
    "mento": {
        "action": "swap",
        "token_in": "CELO",
        "token_out": "USDm",
        "venue": "mento",
    },
    "stcelo": {
        "action": "stake",
        "token_in": "CELO",
        "token_out": "stCELO",
        "venue": "stcelo",
    },
}


def _ubeswap_url(token_in: str, token_out: str) -> str:
    """
    Builds a parametrized Ubeswap deep link.
    Falls back to base URL if either token is not in TOKEN_ADDRESSES.
    """
    addr_in = TOKEN_ADDRESSES.get(token_in)
    addr_out = TOKEN_ADDRESSES.get(token_out)
    if addr_in and addr_out:
        return f"{_UBESWAP_BASE}?inputCurrency={addr_in}&outputCurrency={addr_out}"
    return _UBESWAP_BASE  # safe fallback


# Pre-built CELO → stCELO link for static keyboards (callbacks stake:convert).
UBESWAP_CELO_TO_STCELO_URL = _ubeswap_url("CELO", "stCELO")


def build_venue_links(intent: dict) -> InlineKeyboardMarkup:
    """
    Builds an InlineKeyboardMarkup with url= buttons for the given DeFi intent.
    No transaction signing happens in this bot — all actions open in the user's wallet.

    intent keys used: action, token_in, token_out, venue
    """
    venue = (intent.get("venue") or "").lower()
    token_in = intent.get("token_in", "CELO")
    token_out = intent.get("token_out", "stCELO")

    buttons: list[list[InlineKeyboardButton]] = []

    if venue == "ubeswap" or venue not in ("mento", "stcelo"):
        _treasury_raw = (os.getenv("TREASURY_ADDRESS") or "").strip()
        _treasury = (
            Web3.to_checksum_address(_treasury_raw)
            if _treasury_raw and Web3.is_address(_treasury_raw)
            else ""
        )
        tin, tout = str(token_in), str(token_out)
        addr_in = address_for_symbol(tin) or TOKEN_ADDRESSES.get(tin)
        addr_out = address_for_symbol(tout) or TOKEN_ADDRESSES.get(tout)
        if addr_in and addr_out:
            ubeswap_url = (
                f"{_UBESWAP_BASE}?inputCurrency={addr_in}&outputCurrency={addr_out}"
            )
            if _treasury:
                ubeswap_url += f"&feeTo={_treasury}"
        else:
            ubeswap_url = _UBESWAP_BASE
        buttons.append(
            [
                InlineKeyboardButton(
                    "🔁 Swap on Ubeswap",
                    url=ubeswap_url,
                )
            ]
        )

    if venue == "mento":
        buttons.append(
            [InlineKeyboardButton("💱 Swap on Mento", url=_MENTO_URL)]
        )

    if venue in ("stcelo", "ubeswap"):
        buttons.append(
            [InlineKeyboardButton("📄 stCELO Contract", url=_STCELO_CELOSCAN)]
        )

    buttons.append(
        [InlineKeyboardButton("📱 Open Valora", url=_VALORA_URL)]
    )

    return InlineKeyboardMarkup(buttons)


# --- On-chain Celo trading / DEX shortcuts (AI Trade personalized keyboard) ---

_CELO_CHAIN_ID = 42220
_GALAXY_SWAP = "https://galaxy.exchange/swap"


def _uniswap_swap_url(token_in_sym: str, token_out_sym: str) -> str:
    """Uniswap web: pre-select pair on Celo (``inputCurrency`` / ``outputCurrency``)."""
    addr_in = TOKEN_ADDRESSES.get(token_in_sym)
    addr_out = TOKEN_ADDRESSES.get(token_out_sym)
    base = "https://app.uniswap.org/swap?chain=celo"
    if not addr_in or not addr_out:
        return base
    return f"{base}&inputCurrency={addr_in}&outputCurrency={addr_out}"


def _jumper_swap_url(token_in_sym: str, token_out_sym: str) -> str:
    """LI.FI Jumper: same-chain Celo swap when both tokens are set."""
    addr_in = TOKEN_ADDRESSES.get(token_in_sym)
    addr_out = TOKEN_ADDRESSES.get(token_out_sym)
    if not addr_in or not addr_out:
        return f"https://jumper.exchange/?toChain={_CELO_CHAIN_ID}"
    return (
        f"https://jumper.exchange/"
        f"?fromChain={_CELO_CHAIN_ID}&fromToken={addr_in}"
        f"&toChain={_CELO_CHAIN_ID}&toToken={addr_out}"
    )


def _normalize_for_keywords(text: str) -> str:
    """Lowercase blob for keyword scoring."""
    return " ".join(text.lower().split())


def _score_trading_topics(blob: str) -> dict[str, float]:
    """
    Score article + optional Groq hint for Mento / DeFi / stCELO-Opera themes.

    Returns keys: mento, defi, stcelo, yield_future (all >= 0).
    """
    b = _normalize_for_keywords(blob)
    scores = {"mento": 0.0, "defi": 0.0, "stcelo": 0.0, "yield_future": 0.0}

    mento_kw = (
        "mento",
        "mento protocol",
        "cusd",
        "usd₮",
        "stablecoin",
        "stable swap",
        "euro",
        "eurm",
        "real",
        "brl",
        "regional stable",
    )
    for kw in mento_kw:
        if kw in b:
            scores["mento"] += 1.8

    defi_kw = (
        "defi",
        "dex",
        "amm",
        "liquidity pool",
        "liquidity",
        "swap",
        "swaps",
        "uniswap",
        "ubeswap",
        "lending",
        "borrow",
        "leverage",
        "perpetual",
        "derivative",
    )
    for kw in defi_kw:
        if kw in b:
            scores["defi"] += 1.3

    stcelo_kw = (
        "opera",
        "stcelo",
        "staked celo",
        "stake celo",
        "staking",
        "validator",
        "160m",
        " stake ",
        "stce",
    )
    for kw in stcelo_kw:
        if kw in b:
            scores["stcelo"] += 1.6

    # Strong signal for Opera / large stake commitments (headline-driven AI Trade).
    if "opera" in b:
        scores["stcelo"] += 3.0

    yield_kw = (
        "yield",
        "apy",
        "apr",
        "earn",
        "reward",
        "gain",
        "return",
        "future",
        "upside",
        "growth",
    )
    for kw in yield_kw:
        if kw in b:
            scores["yield_future"] += 0.9

    # Light boost when CELO price / accumulation narrative appears
    if "celo" in b and any(x in b for x in ("buy", "accumulate", "position", "trade")):
        scores["defi"] += 0.5
        scores["stcelo"] += 0.4

    return scores


def _ordered_trading_products(
    scores: dict[str, float],
    pair: tuple[str, str],
) -> list[tuple[str, str]]:
    """
    Build ordered (label, url) pairs for the AI Trade keyboard (max 8, deduped by URL).

    Pair-aware rows (Ubeswap / Uniswap / Jumper) use ``pair`` = (token_in, token_out)
    resolved from Groq suggestions or hint text. Wallets / Mento app root stay generic.
    """
    tin, tout = pair
    mento, defi, stcelo, yld = (
        scores["mento"],
        scores["defi"],
        scores["stcelo"],
        scores["yield_future"],
    )

    ubeswap_pair = (f"🔁 Ubeswap ({tin}→{tout})", _ubeswap_url(tin, tout))
    mento_btn = ("💱 Mento", _MENTO_URL)
    uniswap_btn = ("🦄 Uniswap", _uniswap_swap_url(tin, tout))
    galaxy_btn = ("🌌 Galaxy", _GALAXY_SWAP)
    jumper_btn = ("🔀 Jumper", _jumper_swap_url(tin, tout))
    ubeswap_celo_usdm = (
        "💧 CELO → USDm",
        _ubeswap_url("CELO", "USDm"),
    )
    ubeswap_celo_stcelo = (
        "📈 CELO → stCELO",
        UBESWAP_CELO_TO_STCELO_URL,
    )
    stcelo_scan = ("📄 stCELO token", _STCELO_CELOSCAN)
    valora_btn = ("📱 Valora", _VALORA_URL)

    dominant = max(scores, key=scores.get)
    dom_val = scores[dominant]

    picked: list[tuple[str, str]] = []

    def _add_unique(row: tuple[str, str]) -> None:
        if row[1] in {p[1] for p in picked}:
            return
        picked.append(row)

    # Strong Mento narrative
    if dominant == "mento" and dom_val >= 1.5 and mento >= defi - 0.5:
        for p in (
            mento_btn,
            ubeswap_celo_usdm,
            ubeswap_pair,
            jumper_btn,
            uniswap_btn,
            valora_btn,
        ):
            _add_unique(p)
        logger.info(
            "[AI_TRADE] profile=mento pair=%s/%s scores=%s",
            tin,
            tout,
            scores,
        )

    # Opera / stake / stCELO (e.g. Opera commits, validator news)
    elif dominant == "stcelo" and dom_val >= 1.5:
        for p in (
            ubeswap_celo_stcelo,
            stcelo_scan,
            mento_btn,
            ubeswap_pair,
            jumper_btn,
            valora_btn,
        ):
            _add_unique(p)
        logger.info(
            "[AI_TRADE] profile=stcelo_opera pair=%s/%s scores=%s",
            tin,
            tout,
            scores,
        )

    # DeFi-only set (exclude heavy Mento emphasis unless also scored)
    elif dominant == "defi" and defi >= 1.2 and defi > mento:
        for p in (
            ubeswap_pair,
            uniswap_btn,
            galaxy_btn,
            jumper_btn,
            ubeswap_celo_stcelo,
            valora_btn,
        ):
            _add_unique(p)
        logger.info(
            "[AI_TRADE] profile=defi pair=%s/%s scores=%s",
            tin,
            tout,
            scores,
        )

    # Yield / future upside — emphasize routes to earn + aggregators
    elif dominant == "yield_future" and yld >= 1.5:
        for p in (
            ubeswap_celo_stcelo,
            ubeswap_pair,
            mento_btn,
            jumper_btn,
            uniswap_btn,
            valora_btn,
        ):
            _add_unique(p)
        logger.info(
            "[AI_TRADE] profile=yield pair=%s/%s scores=%s",
            tin,
            tout,
            scores,
        )

    else:
        # Balanced default: core Celo on-chain venues
        for p in (
            ubeswap_pair,
            mento_btn,
            uniswap_btn,
            ubeswap_celo_stcelo,
            galaxy_btn,
            jumper_btn,
            valora_btn,
        ):
            _add_unique(p)
        logger.info(
            "[AI_TRADE] profile=balanced pair=%s/%s scores=%s",
            tin,
            tout,
            scores,
        )

    return picked[:8]


def _pairs_to_keyboard(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Two url buttons per row."""
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(rows), 2):
        chunk = rows[i : i + 2]
        line = [
            InlineKeyboardButton(label[:64], url=url) for label, url in chunk
        ]
        buttons.append(line)
    return InlineKeyboardMarkup(buttons)


def build_personalized_trading_dex_keyboard(
    title: str,
    article_text: str,
    groq_hint: str = "",
    suggestions: list[dict] | None = None,
) -> InlineKeyboardMarkup:
    """
    Build a 2-per-row keyboard of on-chain Celo trading / DEX links from article context.

    Uses keyword scoring on title + body + optional Groq venue/tokens/labels.
    Ubeswap / Uniswap / Jumper rows use a pair resolved from ``suggestions`` first,
    then token names in ``groq_hint``, else CELO → USDm.
    """
    blob = f"{title}\n{article_text}\n{groq_hint}"
    scores = _score_trading_topics(blob)
    pair = resolve_swap_pair_from_suggestions(suggestions, groq_hint)
    rows = _ordered_trading_products(scores, pair)
    return _pairs_to_keyboard(rows)
