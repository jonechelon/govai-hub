# src/utils/url_fetch.py
# Async HTTP fetch + plain text extraction for AI Trade article context.

from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; GovAI-Hub/1.0; +https://github.com/celo-org)"
)

# In-memory URL text cache (Package 1: lazy fill on first fetch, TTL per entry).
_URL_TEXT_CACHE_TTL_SEC = 24 * 60 * 60
_URL_TEXT_CACHE_MAX_KEYS = 300
_url_text_cache: dict[str, tuple[float, str]] = {}
_url_text_cache_lock = asyncio.Lock()


async def _fetch_url_text_uncached(url: str, max_chars: int = 12000) -> str:
    """Fetch a URL and return visible text stripped from HTML (no cache)."""
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(
                url,
                allow_redirects=True,
                max_redirects=5,
            ) as resp:
                if resp.status >= 400:
                    logger.debug("[URL_FETCH] HTTP %s for %s", resp.status, url)
                    return ""
                raw = await resp.text(errors="replace")
    except Exception as exc:
        logger.warning("[URL_FETCH] Request failed | url=%s | error=%s", url, exc)
        return ""

    if len(raw) > 2_000_000:
        logger.debug("[URL_FETCH] Response too large | url=%s", url)
        return ""

    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return ""
        return text[:max_chars]
    except Exception as exc:
        logger.warning("[URL_FETCH] Parse failed | url=%s | error=%s", url, exc)
        return ""


def _cache_sweep_expired() -> None:
    """Remove expired entries (caller holds lock)."""
    now = time.time()
    dead = [k for k, (exp, _) in _url_text_cache.items() if exp <= now]
    for k in dead:
        del _url_text_cache[k]


def _cache_evict_if_full() -> None:
    """Drop oldest entries if over max size (caller holds lock)."""
    while len(_url_text_cache) > _URL_TEXT_CACHE_MAX_KEYS:
        oldest_key = min(
            _url_text_cache,
            key=lambda k: _url_text_cache[k][0],
        )
        del _url_text_cache[oldest_key]


async def fetch_url_text(url: str, max_chars: int = 12000) -> str:
    """Fetch a URL and return visible text stripped from HTML.

    Uses an in-process memory cache (24h TTL) for non-empty results so repeat
    requests for the same URL skip the network (Package 1).

    Returns an empty string on network errors, non-HTML, or oversized responses.

    Args:
        url: HTTP(S) URL to fetch.
        max_chars: Maximum characters to return (truncated after extraction).

    Returns:
        Plain text suitable for LLM context, or empty string if unavailable.
    """
    key = url.strip()
    if not key:
        return ""

    async with _url_text_cache_lock:
        _cache_sweep_expired()
        hit = _url_text_cache.get(key)
        if hit is not None:
            expires_at, cached_text = hit
            if time.time() < expires_at:
                logger.debug(
                    "[URL_FETCH] Memory cache hit | url=%s",
                    key[:120] + ("…" if len(key) > 120 else ""),
                )
                return cached_text[:max_chars] if len(cached_text) > max_chars else cached_text
            del _url_text_cache[key]

    text = await _fetch_url_text_uncached(key, max_chars=max_chars)

    if text.strip():
        async with _url_text_cache_lock:
            _cache_sweep_expired()
            _cache_evict_if_full()
            _url_text_cache[key] = (time.time() + _URL_TEXT_CACHE_TTL_SEC, text)
            logger.debug(
                "[URL_FETCH] Memory cache set | ttl_h=%s | url=%s",
                _URL_TEXT_CACHE_TTL_SEC / 3600,
                key[:120] + ("…" if len(key) > 120 else ""),
            )

    return text
