# src/fetchers/rss_fetcher.py
# Celo GovAI Hub — RSS fetcher for Celo ecosystem feeds (P11)

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

import aiohttp
import feedparser

from src.utils.config_loader import CONFIG
from src.utils.paths import RSS_CACHE_PATH

logger = logging.getLogger(__name__)


def _parse_published(entry: dict) -> str:
    """Parse feed entry date to ISO 8601 string. Never raises.

    Tries: published_parsed -> updated_parsed -> published string -> utcnow.

    Args:
        entry: feedparser entry dict.

    Returns:
        ISO 8601 date string (e.g. "2026-03-13T10:00:00").
    """
    try:
        parsed = entry.get("published_parsed")
        if parsed and len(parsed) >= 6:
            return datetime(*parsed[:6]).isoformat()
    except (TypeError, ValueError):
        pass

    try:
        parsed = entry.get("updated_parsed")
        if parsed and len(parsed) >= 6:
            return datetime(*parsed[:6]).isoformat()
    except (TypeError, ValueError):
        pass

    published_str = entry.get("published", "")
    if isinstance(published_str, str) and published_str.strip():
        return published_str.strip()

    return datetime.utcnow().isoformat()


class RSSFetcher:
    """Fetches and caches RSS/Atom feed items from Celo ecosystem sources."""

    CACHE_FILE = RSS_CACHE_PATH
    CACHE_TTL_MINUTES = 30
    MAX_ITEMS_PER_FEED = 5
    REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Celo GovAI Hub/1.1)"}

    async def fetch_all(self) -> list[dict]:
        """Fetch all configured RSS feeds, with cache and parallel requests.

        Returns:
            List of items, each with title, url, source, source_app, category, published.
            Sorted by published descending. Empty list on total failure.
        """
        cached = self._load_cache()
        if cached is not None:
            items = cached.get("items", [])
            age = (time.time() - cached.get("saved_at", 0)) / 60
            logger.info("[RSS] Cache hit — %s items (age: %.1f min)", len(items), age)
            return items

        feeds = CONFIG.get("rss_feeds", [])
        all_items: list[dict] = []

        async with aiohttp.ClientSession(
            headers=self.HEADERS, timeout=self.REQUEST_TIMEOUT
        ) as session:
            tasks = [self._fetch_feed(session, feed) for feed in feeds]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for feed, result in zip(feeds, results):
            if isinstance(result, Exception):
                logger.warning(
                    "[RSS] %s: fetch failed — %s",
                    feed.get("source", "unknown"),
                    result,
                    exc_info=False,
                )
                continue
            all_items.extend(result)

        # Sort by published descending (most recent first)
        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        self._save_cache(all_items)
        return all_items

    async def _fetch_feed(
        self, session: aiohttp.ClientSession, feed: dict
    ) -> list[dict]:
        """Fetch a single feed and return normalized items."""
        url = feed.get("url", "")
        source = feed.get("source", "unknown")

        try:
            resp = await session.get(url)
        except Exception as e:
            logger.warning("[RSS] %s: request failed — %s", source, e)
            raise

        if resp.status != 200:
            logger.warning("[RSS] %s: HTTP %s — skipping", source, resp.status)
            return []

        raw_content = await resp.text()
        loop = asyncio.get_event_loop()
        parsed = await loop.run_in_executor(
            None, feedparser.parse, raw_content
        )

        logger.debug(
            "[RSS] %s: status=%s entries=%s",
            source,
            resp.status,
            len(parsed.entries),
        )

        items: list[dict] = []
        for entry in parsed.entries[: self.MAX_ITEMS_PER_FEED]:
            link = entry.get("link") or entry.get("href", "")
            if not link:
                continue
            item = {
                "title": (entry.get("title") or "No title").strip(),
                "url": link,
                "source": feed["source"],
                "source_app": feed["source_app"].lower(),
                "category": feed["category"],
                "published": _parse_published(entry),
            }
            items.append(item)

        logger.info("[RSS] %s: %s items fetched", source, len(items))
        return items

    def _load_cache(self) -> dict | None:
        """Load cache from disk if present and not expired.

        Returns:
            Cache dict with saved_at and items, or None if missing/expired.
        """
        if not self.CACHE_FILE.exists():
            return None
        try:
            data = json.loads(self.CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        saved_at = data.get("saved_at", 0)
        age_minutes = (time.time() - saved_at) / 60
        if age_minutes > self.CACHE_TTL_MINUTES:
            return None
        return data

    def _save_cache(self, items: list[dict]) -> None:
        """Persist items to cache file. Logs warning on failure, does not raise."""
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": time.time(), "items": items}
            self.CACHE_FILE.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("[RSS] Cache save failed — %s", e)
