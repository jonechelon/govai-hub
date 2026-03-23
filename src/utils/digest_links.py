# src/utils/digest_links.py
# Shared digest link extraction and Daily Sources HTML for AI Trade / cache.

from __future__ import annotations

import logging
import re

from src.utils.cache_manager import cache
from src.utils.config_loader import CONFIG
from src.utils.text_utils import hesc, href_attr, truncate

logger = logging.getLogger(__name__)

# GitHub releases for mento-core only — keep a single item (first in digest order).
_MENTO_CORE_RELEASES_MARKER = "github.com/mento-protocol/mento-core/releases"


def _is_mento_core_github_release(url: str) -> bool:
    return _MENTO_CORE_RELEASES_MARKER in url.lower()


# ~2 lines of text on typical phone Telegram clients
_LINK_LABEL_MAX_CHARS = 160


def _strip_trailing_parenthesized_urls(title: str) -> str:
    """Remove trailing ``(https://...)`` blocks often duplicated from RSS titles."""
    t = title.strip()
    while True:
        m = re.search(r"\s*\(\s*https?://[^\)]+\)\s*$", t, re.IGNORECASE)
        if not m:
            break
        t = t[: m.start()].rstrip()
    return t


def _append_source_if_missing(title: str, source: str) -> str:
    """Append `` · {source}`` when the feed title is short or omits the outlet name.

    Skips redundant append if ``source`` is empty, ``Unknown``, or already appears
    in ``title`` (case-insensitive). Keeps GitHub-style tags like ``v.2.6.5`` readable
    alongside ``GitHub Mento``.
    """
    t = title.strip()
    s = (source or "").strip()
    if not s or s.lower() == "unknown":
        return t
    if s.lower() in t.lower():
        return t
    return f"{t} · {s}"


def _link_label_text(raw: str) -> str:
    """Single-line label for ``<a>``; cap length for ~2 visible lines."""
    collapsed = " ".join(raw.split())
    return truncate(collapsed, _LINK_LABEL_MAX_CHARS)


def _max_daily_links() -> int:
    raw = CONFIG.get("digest", {}).get("max_daily_source_links", 15)
    try:
        return max(1, min(int(raw), 25))
    except (TypeError, ValueError):
        return 15


async def extract_links_from_digest(digest_id: str) -> list[dict]:
    """Load digest from cache and extract URLs with title and source.

    Dedupes by URL. For GitHub Mento ``mento-core`` releases only, keeps the first
    release link and skips further release URLs so RSS version spam collapses to one
    row. Other Mento news (e.g. Twitter, blogs) are unchanged.

    Returns:
        Up to ``max_daily_source_links`` dicts with keys: title, url, source,
        and optional display_title (shortened headline).
    """
    data = await cache.get_digest(digest_id)
    if not data:
        logger.warning("[LINKS] Cache not found or expired | digest_id=%s", digest_id)
        return []

    cap = _max_daily_links()

    try:
        sections = data.get("sections", [])
        links: list[dict] = []
        mento_github_release_kept = False

        for section in sections:
            items = section.get("items", [])
            for item in items:
                url = item.get("url") or item.get("link") or ""
                if not url or not url.startswith("http"):
                    continue

                if any(lk["url"] == url for lk in links):
                    continue

                if _is_mento_core_github_release(url):
                    if mento_github_release_kept:
                        continue
                    mento_github_release_kept = True

                title = item.get("title") or item.get("name") or "No title"
                source = (
                    item.get("source")
                    or item.get("source_app")
                    or section.get("category", "")
                    or "Unknown"
                )

                entry: dict = {
                    "title": title.strip() if isinstance(title, str) else "No title",
                    "url": url.strip(),
                    "source": source.strip() if isinstance(source, str) else str(source),
                }
                dt = item.get("display_title")
                if isinstance(dt, str) and dt.strip():
                    entry["display_title"] = dt.strip()

                links.append(entry)

                if len(links) >= cap:
                    return links

        return links

    except (KeyError, TypeError) as exc:
        logger.error(
            "[LINKS] Failed to parse digest cache | digest_id=%s | error=%s",
            digest_id,
            exc,
        )
        return []


def format_daily_sources_html(links: list[dict]) -> str:
    """Build HTML body for the AI Trade — Daily Sources message (hyperlinked titles)."""
    if not links:
        return (
            "💹 <b>AI Trade — Daily Sources</b>\n\n"
            "<i>No links available for today yet.</i>"
        )

    cap = _max_daily_links()
    header = "💹 <b>AI Trade — Daily Sources</b>"
    blocks: list[str] = []

    for idx, item in enumerate(links[:cap], start=1):
        raw_title = (
            item.get("display_title")
            or item.get("title")
            or item.get("source")
            or f"Link {idx}"
        )
        outlet = (
            item.get("source")
            or item.get("source_app")
            or ""
        )
        url = item.get("url") or ""
        safe_href = href_attr(url) if url else ""

        cleaned = _strip_trailing_parenthesized_urls(str(raw_title))
        with_outlet = _append_source_if_missing(cleaned, str(outlet))
        label = _link_label_text(with_outlet)

        if url and safe_href:
            blocks.append(
                f'{idx}. <a href="{safe_href}">{hesc(label)}</a>'
            )
        else:
            blocks.append(f"{idx}. {hesc(label)}")

    return header + "\n\n" + "\n\n".join(blocks)
