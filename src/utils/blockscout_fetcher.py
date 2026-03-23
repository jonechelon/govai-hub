# Blockscout Celo API v2 — public JSON, no API key (fallback when Etherscan is unavailable).

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.utils.text_utils import hesc, href_attr, truncate, truncate_wallet

logger = logging.getLogger(__name__)

_DEFAULT_MAINNET = "https://celo.blockscout.com/api/v2"
_DEFAULT_ALFAJORES = "https://alfajores.blockscout.com/api/v2"

_BLOCKSCOUT_URLS: dict[str, str] = {
    "mainnet": (os.getenv("BLOCKSCOUT_CELO_URL") or _DEFAULT_MAINNET).strip()
    or _DEFAULT_MAINNET,
    "alfajores": (
        os.getenv("BLOCKSCOUT_ALFAJORES_URL")
        or os.getenv("BLOCKSCOUT_CELO_ALFAJORES_URL")
        or _DEFAULT_ALFAJORES
    ).strip()
    or _DEFAULT_ALFAJORES,
}

logger.info("[BLOCKSCOUT] URLs configured: %s", _BLOCKSCOUT_URLS)


def _network_key_for_blockscout(network: str | None) -> str | None:
    """Map app network to Blockscout config key; ``sepolia`` has no Blockscout fetch here."""
    n = (network or "mainnet").strip().lower()
    if n == "sepolia":
        return None
    if n == "alfajores":
        return "alfajores"
    return "mainnet"


def get_blockscout_base(network: str | None) -> str | None:
    """Return Blockscout API v2 base URL for ``mainnet`` / ``alfajores``, or None for Sepolia."""
    key = _network_key_for_blockscout(network)
    if key is None:
        return None
    return _BLOCKSCOUT_URLS.get(key, _BLOCKSCOUT_URLS["mainnet"])


def blockscout_tx_explorer_url(tx_hash: str, network: str | None) -> str:
    """HTTPS tx page on Blockscout (never explorer.celo.org legacy host)."""
    n = (network or "mainnet").strip().lower()
    if n == "alfajores":
        return f"https://alfajores.blockscout.com/tx/{tx_hash}"
    return f"https://celo.blockscout.com/tx/{tx_hash}"


def blockscout_address_explorer_url(address: str, network: str | None) -> str:
    """HTTPS address page on Blockscout."""
    n = (network or "mainnet").strip().lower()
    if n == "alfajores":
        return f"https://alfajores.blockscout.com/address/{address}"
    return f"https://celo.blockscout.com/address/{address}"


async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str],
) -> dict[str, Any] | None:
    """GET JSON from Blockscout v2. Do not pass ``limit`` in params (HTTP 422)."""
    try:
        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            logger.debug("[BLOCKSCOUT] GET %s status=%s", url, r.status)
            if r.status != 200:
                body = await r.text()
                logger.warning("[BLOCKSCOUT] Non-200 body: %s", body[:200])
                return None
            data = await r.json(content_type=None)
            if isinstance(data, dict):
                logger.debug(
                    "[BLOCKSCOUT] items=%d",
                    len(data.get("items", [])),
                )
            return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[BLOCKSCOUT] Exception %s: %s", type(exc).__name__, exc)
        return None


def _native_is_error(tx: dict[str, Any]) -> bool:
    if tx.get("result") == "error":
        return True
    st = str(tx.get("status", "")).lower()
    return st in {"error", "failed"}


def _format_iso_timestamp(ts_raw: str | None) -> str:
    if not ts_raw:
        return "—"
    try:
        normalized = str(ts_raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return hesc(str(ts_raw)[:32])


async def fetch_recent_txs(
    wallet: str,
    *,
    limit: int = 20,
    network: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch recent native + ERC-20 activity via Blockscout API v2 (no API key).

    Args:
        wallet: 0x-prefixed EVM address.
        limit: Max combined rows after merge (each endpoint returns a page; we trim).
        network: ``mainnet`` | ``alfajores`` | ``sepolia`` (sepolia → empty).

    Returns:
        ``{"native": [...], "tokens": [...]}``. On failure, both lists are empty.
    """
    base = get_blockscout_base(network)
    if base is None:
        logger.warning("[BLOCKSCOUT] No Blockscout base for network=%s", network)
        return {"native": [], "tokens": []}

    w = wallet.strip().lower()
    # API v2 rejects unknown query params (e.g. ``limit``); trim in Python.
    tx_url = f"{base.rstrip('/')}/addresses/{w}/transactions"
    tok_url = f"{base.rstrip('/')}/addresses/{w}/token-transfers"

    async with aiohttp.ClientSession() as session:
        native_data = await _get(session, tx_url, {})
        token_data = await _get(session, tok_url, {})

    native_raw = native_data.get("items", []) if isinstance(native_data, dict) else []
    tokens_raw = token_data.get("items", []) if isinstance(token_data, dict) else []

    if not isinstance(native_raw, list):
        native_raw = []
    if not isinstance(tokens_raw, list):
        tokens_raw = []

    # Blockscout v2 rejects ``?limit=`` (HTTP 422); trim in Python after fetch.
    native = native_raw[:limit]
    tokens = tokens_raw[:limit]

    logger.info(
        "[BLOCKSCOUT] fetch_recent_txs | base=%s | native=%d | tokens=%d",
        base,
        len(native),
        len(tokens),
    )

    return {"native": native, "tokens": tokens}


# Mainnet Celo governance-related core contracts (Governance, LockedGold, Election).
# See https://docs.celo.org/tooling/contracts/core-contracts
_GOVERNANCE_LABELS: dict[str, str] = {
    # Governance — vote, upvote, execute
    "0xd533ca259b330c7a88f74e000a3faea2d63b7972": "🗳️ Governance Vote",
    # LockedGold / LockedCelo — lock, unlock, withdraw
    "0x6cc083aed9e3ebe302a6336dbc7c921c9f03349e": "🔒 Locked CELO",
    # Election — validator voting
    "0x8d6677192144292870907e3fa8a5527fe55a7ff6": "🗳️ Validator Election",
}
CELO_GOVERNANCE_CONTRACTS: frozenset[str] = frozenset(_GOVERNANCE_LABELS.keys())

KNOWN_DEFI_CONTRACTS: frozenset[str] = frozenset(
    {
        "0x4aad04d41fd7fd495503731c5a2579e19054c432",  # stCELO
        "0x765de816845861e75a25fca122bb6898b8b1282a",  # cUSD (legacy label USDm in product copy)
        "0xceba9300f2b948710d2653dd7b07f33a8b32118c",  # USDC
        "0x471ece3750da237f93b8e339c536989b8978a438",  # CELO (token contract)
    }
)


def _native_to_address_lower(tx: dict[str, Any]) -> str:
    """Resolve ``to`` address from a Blockscout native tx item (mixed-case safe)."""
    to = tx.get("to")
    if isinstance(to, dict):
        return str(to.get("hash", "")).strip().lower()
    if isinstance(to, str):
        return to.strip().lower()
    return ""


def filter_governance_txs(tx_data: dict[str, list]) -> list[dict]:
    """Return native txs whose recipient is any Celo governance-related core contract."""
    out: list[dict] = []
    for tx in tx_data.get("native", []):
        if not isinstance(tx, dict):
            continue
        if _native_to_address_lower(tx) in CELO_GOVERNANCE_CONTRACTS:
            out.append(tx)
    return out


def _token_contract_address_lower(tx: dict[str, Any]) -> str:
    """Token contract from a Blockscout v2 token-transfer row (``address_hash`` first)."""
    if not isinstance(tx, dict):
        return ""
    t = tx.get("token")
    token = t if isinstance(t, dict) else {}
    return (
        str(
            token.get("address_hash")
            or token.get("address")
            or token.get("contract_address")
            or ""
        )
        .strip()
        .lower()
    )


def filter_defi_txs(tx_data: dict[str, list]) -> list[dict]:
    """Return token transfers whose token contract is in ``KNOWN_DEFI_CONTRACTS``."""
    results: list[dict] = []
    for tx in tx_data.get("tokens", []):
        if not isinstance(tx, dict):
            continue
        addr = _token_contract_address_lower(tx)
        if addr in KNOWN_DEFI_CONTRACTS:
            results.append(tx)
    return results


def filter_etherscan_txlist(txs: list[dict], scope: str) -> list[dict]:
    """Filter Etherscan ``txlist`` rows by scope (``all`` | ``governance`` | ``aitrade``)."""
    if scope == "all":
        return txs
    if scope == "governance":
        return [
            t
            for t in txs
            if str(t.get("to", "")).strip().lower() in CELO_GOVERNANCE_CONTRACTS
        ]
    if scope == "aitrade":
        return [
            t
            for t in txs
            if str(t.get("to", "")).strip().lower() in KNOWN_DEFI_CONTRACTS
        ]
    return txs


# Semantic labels — Blockscout v2 ``method`` (function name) + ``to.hash``.
STCELO_CONTRACT = "0x4aad04d41fd7fd495503731c5a2579e19054c432"
UBESWAP_ROUTER = "0xe3d8bd6aed4f159bc8000a9cd47cffdb95f96121"
MENTO_BROKER = "0x777a8255ca72412f0d706dc03c9d1987306b4cad"

DEFI_CONTRACTS: frozenset[str] = frozenset(
    {STCELO_CONTRACT, UBESWAP_ROUTER, MENTO_BROKER}
)

SWAP_METHODS: frozenset[str] = frozenset(
    {
        "swap",
        "swapexacttokensfortokens",
        "swaptokensforexactcelo",
        "swapexactcelofortokens",
        "swapout",
        "swapin",
    }
)
STAKE_METHODS: frozenset[str] = frozenset({"deposit", "stake", "delegate"})
APPROVAL_METHODS: frozenset[str] = frozenset({"approve", "increaseallowance"})


def classify_tx(tx: dict) -> str:
    """Return a human-readable label for a Blockscout v2 transaction dict.

    Uses ``method`` (function selector name) and ``to.hash`` when present.
    Never raises; safe for native txs and token-transfer rows.
    """
    if not isinstance(tx, dict):
        return "📄 Unknown"

    to_raw = tx.get("to")
    if isinstance(to_raw, dict):
        to_addr = str(to_raw.get("hash", "")).strip().lower()
    elif isinstance(to_raw, str):
        to_addr = to_raw.strip().lower()
    else:
        to_addr = ""

    method_raw = tx.get("method")
    method = str(method_raw or "").strip().lower().replace("_", "")

    if to_addr in CELO_GOVERNANCE_CONTRACTS:
        return _GOVERNANCE_LABELS[to_addr]
    if to_addr == STCELO_CONTRACT or method in STAKE_METHODS:
        return "🥩 Stake stCELO"
    if to_addr in DEFI_CONTRACTS or method in SWAP_METHODS:
        return "🔄 Swap"
    if method in APPROVAL_METHODS:
        return "✅ Approval"
    if not method or method == "0x":
        return "📤 CELO Transfer"
    return f"📄 {method[:20]}"


def format_tx_lines(
    tx_data: dict[str, list],
    wallet: str,
    limit: int = 20,
    network: str | None = None,
) -> list[str]:
    """Format native + token rows into Telegram HTML lines (escaped)."""
    _ = wallet  # reserved for future IN/OUT heuristics
    entries: list[dict[str, Any]] = []

    for tx in tx_data.get("native", []):
        if not isinstance(tx, dict):
            continue
        entries.append(
            {
                "ts": tx.get("timestamp", ""),
                "hash": tx.get("hash", ""),
                "symbol": "CELO",
                "is_error": _native_is_error(tx),
                "raw_tx": tx,
            }
        )

    for tx in tx_data.get("tokens", []):
        if not isinstance(tx, dict):
            continue
        tok = tx.get("token") if isinstance(tx.get("token"), dict) else {}
        sym = tok.get("symbol", "?")
        h = (
            tx.get("transaction_hash")
            or tx.get("tx_hash")
            or tx.get("hash")
            or ""
        )
        entries.append(
            {
                "ts": tx.get("timestamp", ""),
                "hash": str(h),
                "symbol": sym,
                "is_error": False,
                "raw_tx": tx,
            }
        )

    entries.sort(key=lambda x: str(x["ts"]), reverse=True)

    lines: list[str] = []
    for i, e in enumerate(entries[:limit], 1):
        h = str(e.get("hash") or "")
        if not h:
            continue
        short = h[:10] + "…" if len(h) > 10 else h
        status = "❌" if e.get("is_error") else "✅"
        sym_code = hesc(str(e.get("symbol", "?")))
        safe_label = hesc(short)
        safe_url = href_attr(blockscout_tx_explorer_url(h, network))
        raw_label = classify_tx(e.get("raw_tx", {}))
        label_html = hesc(raw_label)
        lines.append(
            f"{i}. {status} <b>{label_html}</b> · "
            f"<code>{sym_code}</code> · "
            f'<a href="{safe_url}">{safe_label}</a>'
        )

    return lines if lines else ["<i>No recent transactions found.</i>"]


def format_blockscout_message_html(
    *,
    header_line: str,
    address: str,
    tx_data: dict[str, list],
    network: str | None,
    max_rows: int = 8,
    empty_message_html: str | None = None,
) -> str:
    """Build full HTML for Blockscout-backed tx view (links on celo.blockscout.com)."""
    lines: list[str] = [header_line + "\n"]
    lines.append(f"<i>Wallet: <code>{hesc(truncate_wallet(address))}</code></i>\n")

    entries: list[dict[str, Any]] = []
    for tx in tx_data.get("native", []):
        if not isinstance(tx, dict):
            continue
        entries.append(
            {
                "ts": str(tx.get("timestamp", "")),
                "hash": str(tx.get("hash", "") or ""),
                "symbol": "CELO",
                "is_error": _native_is_error(tx),
            }
        )
    for tx in tx_data.get("tokens", []):
        if not isinstance(tx, dict):
            continue
        tok = tx.get("token") if isinstance(tx.get("token"), dict) else {}
        sym = tok.get("symbol", "?")
        h = (
            tx.get("transaction_hash")
            or tx.get("tx_hash")
            or tx.get("hash")
            or ""
        )
        entries.append(
            {
                "ts": str(tx.get("timestamp", "")),
                "hash": str(h),
                "symbol": sym,
                "is_error": False,
            }
        )

    entries = [e for e in entries if e.get("hash")]
    entries.sort(key=lambda x: x["ts"], reverse=True)
    shown = entries[:max_rows]

    if not shown:
        empty_body = (
            empty_message_html
            if empty_message_html
            else "<i>No recent transactions in this window.</i>"
        )
        lines.append(f"\n{empty_body}")
        addr_url = _footer_address_href(address, network)
        lines.append(f'\n🔗 <a href="{addr_url}">Full history on explorer</a>')
        return truncate("\n".join(lines), 3500)

    lines.append("")
    net = network
    for i, e in enumerate(shown, start=1):
        h = str(e["hash"])
        ts_disp = _format_iso_timestamp(e.get("ts"))
        status = "❌" if e.get("is_error") else "✅"
        sym = hesc(str(e.get("symbol", "?")))
        short_raw = h[:10] + "…" if len(h) > 10 else h
        safe_label = hesc(short_raw)
        safe_url = href_attr(blockscout_tx_explorer_url(h, net))
        lines.append(
            f"{i}. {status} <b>{sym}</b> · {ts_disp} · "
            f'<a href="{safe_url}">{safe_label}</a>'
        )

    if len(entries) > max_rows:
        lines.append(
            f"\n<i>Showing {max_rows} of {len(entries)} merged entries.</i>"
        )

    addr_url = _footer_address_href(address, network)
    lines.append(f'\n🔗 <a href="{addr_url}">Full history on explorer</a>')
    return truncate("\n".join(lines), 3500)


def _footer_address_href(address: str, network: str | None) -> str:
    """Explorer address link: Blockscout for mainnet/alfajores; legacy explorer for Sepolia."""
    if (network or "").strip().lower() == "sepolia":
        from src.utils.onchain_txlist_format import explorer_address_url

        return href_attr(explorer_address_url(address, network))
    return href_attr(blockscout_address_explorer_url(address, network))
