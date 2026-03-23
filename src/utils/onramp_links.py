# src/utils/onramp_links.py
# Fiat on-ramp deep links (Transak, Ramp) and Celo docs fallback — no server-side signing.

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode

from telegram import InlineKeyboardButton

from src.fetchers.coingecko_prices import (
    STABLECOIN_SYMBOLS_FOR_FIAT,
    normalize_trade_symbol,
)

logger = logging.getLogger(__name__)

# Official Celo directory of ramp providers (no API key).
CELO_RAMPS_DOCS_URL = "https://docs.celo.org/home/ramps"

TRANSAK_BASE = "https://global.transak.com/"
RAMP_BASE = "https://buy.ramp.network/"

# Transak ``cryptoCurrencyCode`` for Celo (partner dashboard / coverage docs).
_TRANSAK_CRYPTO_CODE: dict[str, str] = {
    "USDM": "USDM",
    "CUSD": "CUSD",
    "CEUR": "CEUR",
    "USDC": "USDC",
    "USDT": "USDT",
    "CELO": "CELO",
    "EURC": "EURC",
    "USDGLO": "GLO",
    "GLO": "GLO",
}

# Ramp ``swapAsset`` / ``defaultAsset`` — Celo-prefixed pairs (USDC/USDT only on many regions).
_RAMP_ASSET: dict[str, str] = {
    "USDC": "CELO_USDC",
    "USDT": "CELO_USDT",
}


def _default_fiat_transak() -> str:
    return (os.getenv("DEFAULT_ONRAMP_FIAT") or "USD").strip().upper() or "USD"


def _default_fiat_ramp() -> str:
    raw = os.getenv("DEFAULT_RAMP_FIAT", "").strip().upper()
    return raw or _default_fiat_transak()


def resolve_primary_stable_symbol(
    suggestions: list[dict] | None,
    pair: tuple[str, str],
) -> str | None:
    """Pick one stablecoin symbol from Groq suggestions or resolved swap pair."""
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        for key in ("token_in", "token_out"):
            sym = normalize_trade_symbol(s.get(key))
            if sym and sym in STABLECOIN_SYMBOLS_FOR_FIAT:
                return sym
    for sym in pair:
        if sym in STABLECOIN_SYMBOLS_FOR_FIAT:
            return sym
    return None


def should_offer_onramp_shortcuts(
    suggestions: list[dict] | None,
    pair: tuple[str, str],
) -> bool:
    """True when stablecoins appear in suggestions or in the resolved DEX pair."""
    return resolve_primary_stable_symbol(suggestions, pair) is not None


def build_transak_buy_url(
    stable_symbol: str,
    *,
    fiat_currency: str | None = None,
    wallet_address: str | None = None,
) -> str | None:
    """Transak widget URL with chain + asset + fiat. Requires ``TRANSAK_API_KEY``."""
    api_key = (os.getenv("TRANSAK_API_KEY") or "").strip()
    if not api_key:
        return None
    code = _TRANSAK_CRYPTO_CODE.get(stable_symbol.upper())
    if not code:
        return None
    fiat = (fiat_currency or _default_fiat_transak()).upper()
    params: dict[str, str] = {
        "apiKey": api_key,
        "network": "celo",
        "cryptoCurrencyCode": code,
        "fiatCurrency": fiat,
        "defaultPaymentMethod": "credit_debit_card",
    }
    wa = (wallet_address or "").strip()
    if wa.startswith("0x") and len(wa) == 42:
        params["walletAddress"] = wa
    return f"{TRANSAK_BASE}?{urlencode(params)}"


def build_ramp_buy_url(
    stable_symbol: str,
    *,
    fiat_currency: str | None = None,
    wallet_address: str | None = None,
) -> str | None:
    """Ramp buy URL for CELO_USDC / CELO_USDT. Requires ``RAMP_HOST_API_KEY``."""
    api_key = (os.getenv("RAMP_HOST_API_KEY") or "").strip()
    if not api_key:
        return None
    asset = _RAMP_ASSET.get(stable_symbol.upper())
    if not asset:
        return None
    fiat = (fiat_currency or _default_fiat_ramp()).upper()
    params: dict[str, str] = {
        "hostApiKey": api_key,
        "swapAsset": asset,
        "defaultAsset": asset,
        "fiatCurrency": fiat,
    }
    wa = (wallet_address or "").strip()
    if wa.startswith("0x") and len(wa) == 42:
        params["userAddress"] = wa
    return f"{RAMP_BASE}?{urlencode(params)}"


def build_onramp_keyboard_rows(
    suggestions: list[dict] | None,
    pair: tuple[str, str],
    *,
    wallet_address: str | None = None,
) -> list[list[InlineKeyboardButton]]:
    """Optional row(s) of fiat on-ramp + Celo docs when stablecoins are in context."""
    if not should_offer_onramp_shortcuts(suggestions, pair):
        return []

    stable = resolve_primary_stable_symbol(suggestions, pair)
    if not stable:
        return []

    buttons: list[InlineKeyboardButton] = []

    t_url = build_transak_buy_url(stable, wallet_address=wallet_address)
    if t_url:
        buttons.append(
            InlineKeyboardButton(
                "💳 Buy with fiat · Transak",
                url=t_url,
            )
        )

    r_url = build_ramp_buy_url(stable, wallet_address=wallet_address)
    if r_url:
        buttons.append(
            InlineKeyboardButton(
                "🌉 Buy with fiat · Ramp",
                url=r_url,
            )
        )

    buttons.append(
        InlineKeyboardButton(
            "📚 Celo on-ramps (directory)",
            url=CELO_RAMPS_DOCS_URL,
        )
    )

    if not buttons:
        return []

    # Up to 3 buttons in one row (Telegram limit per row is flexible).
    return [buttons]
