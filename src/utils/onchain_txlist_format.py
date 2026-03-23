# Helpers for formatting Celo address transaction lists (Telegram HTML).

from __future__ import annotations

from datetime import datetime, timezone

from src.utils.text_utils import hesc, href_attr, truncate, truncate_wallet

# Etherscan V2 chain IDs for Celo networks.
NETWORK_TO_CHAIN_ID: dict[str, str] = {
    "mainnet": "42220",
    "alfajores": "44787",
    "sepolia": "11142220",
}


def chain_id_for_network(network: str | None) -> str:
    """Map ``users.preferred_network`` to an Etherscan V2 chain id."""
    n = (network or "mainnet").strip().lower()
    return NETWORK_TO_CHAIN_ID.get(n, NETWORK_TO_CHAIN_ID["mainnet"])


def explorer_base_for_network(network: str | None) -> str:
    """Return the canonical block explorer base URL for the user's network."""
    n = (network or "mainnet").strip().lower()
    if n == "alfajores":
        return "https://alfajores.celoscan.io"
    if n == "sepolia":
        return "https://celo-sepolia.blockscout.com"
    return "https://celoscan.io"


def explorer_address_url(address: str, network: str | None) -> str:
    """HTTPS URL to the wallet page on the network explorer."""
    return f"{explorer_base_for_network(network)}/address/{address}"


def explorer_tx_url(tx_hash: str, network: str | None) -> str:
    """HTTPS URL to a single transaction on the network explorer."""
    return f"{explorer_base_for_network(network)}/tx/{tx_hash}"


def _short_hash(tx_hash: str) -> str:
    h = tx_hash.strip()
    if len(h) < 18:
        return h or "—"
    return f"{h[:10]}…{h[-6:]}"


def _format_ts(ts_raw: str | int | None) -> str:
    try:
        sec = int(ts_raw)
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return "—"


def format_txlist_message_html(
    *,
    header_line: str,
    address: str,
    txs: list[dict],
    network: str | None,
    max_rows: int = 8,
    empty_message_html: str | None = None,
) -> str:
    """Build HTML body for recent normal transactions (Etherscan ``txlist`` rows)."""
    addr_lower = address.lower()
    lines: list[str] = [header_line + "\n"]
    lines.append(f"<i>Wallet: <code>{hesc(truncate_wallet(address))}</code></i>\n")

    if not txs:
        empty_body = (
            empty_message_html
            if empty_message_html
            else "<i>No recent transactions in this window.</i>"
        )
        lines.append(f"\n{empty_body}")
        lines.append(
            f"\n🔗 <a href=\"{href_attr(explorer_address_url(address, network))}\">"
            "Full history on explorer</a>"
        )
        return truncate("\n".join(lines), 3500)

    shown = txs[:max_rows]
    lines.append("")
    for i, tx in enumerate(shown, start=1):
        h = str(tx.get("hash", "") or "")
        ts = _format_ts(tx.get("timeStamp"))
        to_addr = str(tx.get("to", "") or "").lower()
        from_addr = str(tx.get("from", "") or "").lower()
        if addr_lower and to_addr == addr_lower and from_addr != addr_lower:
            direction = "IN"
        elif addr_lower and from_addr == addr_lower:
            direction = "OUT"
        else:
            direction = "—"
        val_wei = tx.get("value", "0")
        try:
            val_celo = int(val_wei) / 1e18
            val_s = f"{val_celo:.4f} CELO"
        except (TypeError, ValueError):
            val_s = "—"
        safe_url = href_attr(explorer_tx_url(h, network))
        safe_label = hesc(_short_hash(h))
        lines.append(
            f"{i}. <a href=\"{safe_url}\">{safe_label}</a> · {hesc(ts)} · "
            f"{hesc(direction)} · {hesc(val_s)}"
        )

    if len(txs) > max_rows:
        lines.append(f"\n<i>Showing {max_rows} of {len(txs)} fetched.</i>")

    lines.append(
        f"\n🔗 <a href=\"{href_attr(explorer_address_url(address, network))}\">"
        "Full history on explorer</a>"
    )
    return truncate("\n".join(lines), 3500)
