# src/fetchers/defillama_celo_tvl.py
# Celo chain TVL from DeFi Llama (free /v2/chains) — used for staking context in AI Trade.

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_DEFILLAMA_CHAINS = "https://api.llama.fi/v2/chains"
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_USER_AGENT = "Mozilla/5.0 (compatible; Celo GovAI Hub/1.2)"


async def fetch_celo_chain_tvl_usd() -> float:
    """Return Celo chain TVL in USD from DeFi Llama, or 0.0 on failure.

    Uses the same public endpoint as ``MarketFetcher``; no API key required.
    """
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        ) as session:
            async with session.get(_DEFILLAMA_CHAINS) as resp:
                if resp.status != 200:
                    logger.warning("[DEFILLAMA] chains HTTP %s", resp.status)
                    return 0.0
                chains: Any = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("[DEFILLAMA] Celo TVL fetch failed: %s", exc)
        return 0.0

    if not isinstance(chains, list):
        return 0.0

    for c in chains:
        if not isinstance(c, dict):
            continue
        if str(c.get("name", "")).lower() == "celo":
            try:
                tvl = float(c.get("tvl", 0.0))
            except (TypeError, ValueError):
                return 0.0
            if tvl > 0:
                logger.debug("[DEFILLAMA] Celo TVL = %.0f", tvl)
            return max(0.0, tvl)
    return 0.0


def format_tvl_usd(tvl: float) -> str:
    """Human-readable TVL (e.g. ``$142.50M``)."""
    if tvl >= 1e9:
        return f"${tvl / 1e9:.2f}B"
    if tvl >= 1e6:
        return f"${tvl / 1e6:.2f}M"
    if tvl >= 1e3:
        return f"${tvl / 1e3:.2f}K"
    return f"${tvl:.0f}"
