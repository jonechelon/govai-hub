# src/fetchers/coingecko_prices.py
# CoinGecko simple/price for AI Trade digest spot references (optional COINGECKO_API_KEY).

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.coingecko.com/api/v3"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
_USER_AGENT = "Mozilla/5.0 (compatible; Celo GovAI Hub/1.2)"

# Symbols treated as stablecoins for multi-fiat spot lines and on-ramp UI hints.
STABLECOIN_SYMBOLS_FOR_FIAT: frozenset[str] = frozenset(
    {
        "USDM",
        "CUSD",
        "CEUR",
        "USDC",
        "USDT",
        "EURC",
        "USDGLO",
        "GLO",
        "CREAL",
        "G",
    }
)

# CoinGecko ``ids`` for /simple/price — Celo-native and common cross-refs.
_COINGECKO_ID_BY_SYMBOL: dict[str, str] = {
    "CELO": "celo",
    "STCELO": "celo",
    "CGLD": "celo",
    "USDM": "celo-dollar",
    "CUSD": "celo-dollar",
    "CEUR": "celo-euro",
    "CREAL": "celo-real-creal",
    "USDC": "usd-coin",
    "USDT": "tether",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "WETH": "weth",
    "WBTC": "wrapped-bitcoin",
    "EURC": "euro-coin",
    "SOL": "solana",
    "GLO": "glo-dollar",
    "USDGLO": "glo-dollar",
    "UNI": "uniswap",
    "SUSHI": "sushi",
    "CRV": "curve-dao-token",
    "PACT": "impactmarket",
    "MOO": "moola-market",
    "JMPT": "jumptoken",
}

# Order of extra fiat columns in UI (after USD) when present in API response.
_FIAT_DISPLAY_ORDER: tuple[str, ...] = ("eur", "brl", "gbp", "mxn")

# In-process cache for CoinGecko simple/price bundles (TTL seconds).
_COINGECKO_CACHE_TTL_SEC = int(os.getenv("COINGECKO_CACHE_TTL_SEC", "600"))
_cg_bundle_cache: dict[str, tuple[float, dict[str, dict[str, float | None]]]] = {}
_cg_cache_lock = asyncio.Lock()


def normalize_trade_symbol(raw: str | None) -> str:
    """Uppercase token symbol for lookup (e.g. stCELO → STCELO)."""
    if not raw or not isinstance(raw, str):
        return ""
    return raw.strip().upper()


def collect_symbols_from_trade_suggestions(suggestions: list[dict]) -> set[str]:
    """Gather token_in / token_out symbols from Groq suggestion dicts."""
    out: set[str] = set()
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        for key in ("token_in", "token_out"):
            sym = normalize_trade_symbol(s.get(key))
            if sym and sym in _COINGECKO_ID_BY_SYMBOL:
                out.add(sym)
    return out


def _coingecko_headers() -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    key = (os.getenv("COINGECKO_API_KEY") or "").strip()
    if not key:
        return headers
    base = (os.getenv("COINGECKO_API_BASE") or _DEFAULT_BASE).lower()
    if "pro-api.coingecko.com" in base:
        headers["x-cg-pro-api-key"] = key
    else:
        headers["x-cg-demo-api-key"] = key
    return headers


def _coingecko_api_base() -> str:
    raw = (os.getenv("COINGECKO_API_BASE") or _DEFAULT_BASE).strip().rstrip("/")
    return raw or _DEFAULT_BASE


def _vs_currencies_param() -> str:
    """Comma-separated vs_currencies for /simple/price (always includes usd)."""
    raw = (os.getenv("COINGECKO_FIAT_CURRENCIES") or "usd,eur,brl,gbp,mxn").strip()
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if "usd" not in parts:
        parts.insert(0, "usd")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ",".join(out)


def _cache_key(symbols: set[str], vs_param: str) -> str:
    return f"{','.join(sorted(symbols))}|{vs_param}"


async def fetch_trade_token_market_stats(
    symbols: set[str],
) -> dict[str, dict[str, float | None]]:
    """Fetch spot prices vs USD + major fiats and optional 24h %% change (USD) per symbol.

    Uses an in-process TTL cache (default 10 minutes) to respect rate limits.
    """
    if not symbols:
        return {}

    vs_param = _vs_currencies_param()
    key = _cache_key(symbols, vs_param)
    now = time.time()
    async with _cg_cache_lock:
        hit = _cg_bundle_cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]

    data = await _fetch_coingecko_simple_bundle(symbols, vs_param=vs_param)
    async with _cg_cache_lock:
        _cg_bundle_cache[key] = (now + _COINGECKO_CACHE_TTL_SEC, data)
    return data


async def fetch_trade_token_spot_usd(symbols: set[str]) -> dict[str, float]:
    """Map symbol → USD spot (compat helper for callers that only need price)."""
    stats = await fetch_trade_token_market_stats(symbols)
    return {k: float(v["usd"]) for k, v in stats.items() if v.get("usd") is not None}


def _float_or_none(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


async def _fetch_coingecko_simple_bundle(
    symbols: set[str],
    *,
    vs_param: str,
) -> dict[str, dict[str, float | None]]:
    """CoinGecko ``simple/price`` with multiple ``vs_currencies``."""
    id_to_symbols: dict[str, list[str]] = {}
    for sym in symbols:
        cg_id = _COINGECKO_ID_BY_SYMBOL.get(sym)
        if not cg_id:
            continue
        id_to_symbols.setdefault(cg_id, []).append(sym)

    ids_param = ",".join(sorted(id_to_symbols.keys()))
    if not ids_param:
        return {}

    url = f"{_coingecko_api_base()}/simple/price"
    params = {
        "ids": ids_param,
        "vs_currencies": vs_param,
        "include_24hr_change": "true",
    }
    headers = _coingecko_headers()

    try:
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        ) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "[COINGECKO] simple/price HTTP %s — %s",
                        resp.status,
                        body[:120],
                    )
                    return {}
                raw: Any = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("[COINGECKO] simple/price failed: %s", exc)
        return {}

    if not isinstance(raw, dict):
        return {}

    vs_keys = [x.strip().lower() for x in vs_param.split(",") if x.strip()]

    out: dict[str, dict[str, float | None]] = {}
    for cg_id, sym_list in id_to_symbols.items():
        block = raw.get(cg_id)
        if not isinstance(block, dict):
            continue
        usd = _float_or_none(block.get("usd"))
        if usd is None:
            continue
        chg_raw = block.get("usd_24h_change")
        chg: float | None
        try:
            chg = float(chg_raw) if chg_raw is not None else None
        except (TypeError, ValueError):
            chg = None

        row: dict[str, float | None] = {
            "usd": usd,
            "usd_24h_change": chg,
        }
        for vk in vs_keys:
            if vk == "usd":
                continue
            row[vk] = _float_or_none(block.get(vk))

        for sym in sym_list:
            out[sym] = dict(row)

    if out:
        logger.info("[COINGECKO] market stats (multi-fiat) for %s", sorted(out.keys()))
    return out


def format_usd_price(price: float) -> str:
    """Format a spot price for display (compact, English locale style)."""
    if price >= 1:
        return f"{price:.2f}"
    if price >= 0.01:
        return f"{price:.4f}"
    return f"{price:.6f}".rstrip("0").rstrip(".")


def format_fiat_quote(currency_key: str, price: float) -> str:
    """Format one fiat quote for display (English labels)."""
    c = currency_key.lower()
    if c == "usd":
        return f"${format_usd_price(price)}"
    if c == "eur":
        return f"€{format_usd_price(price)}"
    if c == "brl":
        return f"R${format_usd_price(price)}"
    if c == "gbp":
        return f"£{format_usd_price(price)}"
    if c == "mxn":
        return f"MX${format_usd_price(price)}"
    return f"{format_usd_price(price)} {c.upper()}"


def stablecoin_fiat_spot_fragment(
    stats_row: dict[str, float | None] | None,
    sym: str,
) -> str:
    """Compact multi-fiat fragment for stablecoins (empty if not applicable)."""
    if sym not in STABLECOIN_SYMBOLS_FOR_FIAT or not stats_row:
        return ""
    parts: list[str] = []
    usd = stats_row.get("usd")
    if usd is not None:
        parts.append(f"${format_usd_price(float(usd))}")
    for fk in _FIAT_DISPLAY_ORDER:
        v = stats_row.get(fk)
        if v is not None:
            parts.append(format_fiat_quote(fk, float(v)))
    if len(parts) < 2:
        return ""
    return " · ".join(parts[:5])


def enrich_stable_line_with_fiat(
    base_line: str,
    sym: str,
    stats: dict[str, dict[str, float | None]],
) -> str:
    """Append multi-fiat reference for stablecoin lines when data is available."""
    if sym not in STABLECOIN_SYMBOLS_FOR_FIAT:
        return base_line
    row = stats.get(sym)
    frag = stablecoin_fiat_spot_fragment(row, sym)
    if not frag:
        return base_line
    return f"{base_line} · Ref: {frag}"


def is_stablecoin_symbol(sym: str) -> bool:
    """True if ``sym`` is treated as a stablecoin for fiat/on-ramp UX."""
    return sym.upper() in STABLECOIN_SYMBOLS_FOR_FIAT
