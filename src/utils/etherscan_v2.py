# src/utils/etherscan_v2.py
# Optional Etherscan API V2 for Celo mainnet (chainid 42220) — transaction history, etc.

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def get_etherscan_v2_config() -> tuple[str | None, str, str]:
    """Return (api_key or None, base_url, chain_id string)."""
    key = os.getenv("ETHERSCAN_API_KEY", "").strip() or None
    base = (
        os.getenv("ETHERSCAN_V2_URL", "https://api.etherscan.io/v2/api").strip()
        or "https://api.etherscan.io/v2/api"
    )
    chain = os.getenv("CELO_CHAIN_ID", "42220").strip() or "42220"
    return key, base, chain


async def fetch_address_txlist(
    address: str,
    *,
    page: int = 1,
    offset: int = 10,
    chain_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Fetch normal transactions for an address on Celo via Etherscan V2.

    Returns parsed JSON ``result`` payload on success, or None if no API key,
    HTTP error, or API error status.

    Args:
        address: Checksum or lowercase 0x address.
        page: Page number for pagination.
        offset: Max rows per page (Etherscan caps apply).
        chain_id: Etherscan V2 ``chainid`` (e.g. Celo mainnet ``42220``). Defaults to ``CELO_CHAIN_ID``.

    Returns:
        API JSON ``result`` (often a list of txs) or None.
    """
    key, base, _default_chain = get_etherscan_v2_config()
    if not key:
        logger.debug("[ETHERSCAN_V2] ETHERSCAN_API_KEY not set — skip txlist")
        return None

    chain = (chain_id or _default_chain).strip() or _default_chain

    params: dict[str, str | int] = {
        "chainid": chain,
        "module": "account",
        "action": "txlist",
        "address": address,
        "page": page,
        "offset": offset,
        "sort": "desc",
        "apikey": key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                base,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                raw = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("[ETHERSCAN_V2] Request failed | address=%s | %s", address, exc)
        return None

    status = raw.get("status")
    if str(status) != "1":
        msg = raw.get("message", "unknown")
        logger.debug(
            "[ETHERSCAN_V2] Non-success | address=%s | message=%s",
            address,
            msg,
        )
        return None

    return raw.get("result")
