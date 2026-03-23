# src/utils/text_utils.py
# Canonical HTML formatting helpers for GovAI Hub Telegram messages.
# All functions must be imported from here — never duplicate inline.

from html import escape as _escape


def hesc(text: str) -> str:
    """HTML-escape dynamic content for Telegram ParseMode.HTML."""
    return _escape(str(text))


def href_attr(url: str) -> str:
    """Escape a URL for use in an HTML ``href`` attribute (Telegram HTML)."""
    return str(url).replace("&", "&amp;").replace('"', "&quot;")


def truncate(text: str, limit: int = 800) -> str:
    """Truncate text for Telegram message body; appends ellipsis if cut."""
    return text[:limit] + "…" if len(text) > limit else text


def truncate_wallet(addr: str) -> str:
    """Display-safe wallet address format: 0x1234…abcd"""
    return f"{addr[:6]}…{addr[-4:]}" if len(addr) > 10 else addr


def proposal_header(proposal_id: int, title: str, status: str) -> str:
    """Build the standard HTML header block for any proposal view."""
    status_emoji = {
        "ACTIVE": "🟢",
        "REJECTED": "🔴",
        "EXECUTED": "✅",
        "EXPIRED": "⏰",
    }.get(status.upper(), "⏳")  # default: ⏳ Pending
    return (
        f"<b>{status_emoji} Proposal #{hesc(str(proposal_id))}</b>\n"
        f"<b>{hesc(truncate(title, 120))}</b>"
    )
