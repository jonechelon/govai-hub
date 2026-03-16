# src/fetchers/twitter_fetcher.py
# Up-to-Celo — Twitter fetcher via Nitter RSS (P12)

from __future__ import annotations

import asyncio
import json
import logging
import time
import aiohttp
import feedparser

from src.fetchers.rss_fetcher import _parse_published
from src.utils.config_loader import CONFIG
from src.utils.paths import TWITTER_CACHE_PATH

logger = logging.getLogger(__name__)


class TwitterFetcher:
    """Fetches tweets from Celo ecosystem accounts via Nitter RSS instances."""

    CACHE_FILE = TWITTER_CACHE_PATH
    CACHE_TTL_MINUTES = 30
    MAX_ITEMS_PER_ACCOUNT = 5
    REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Up-to-Celo/1.1)"}

    async def fetch_all(self) -> list[dict]:
        """Fetch all configured Twitter accounts via Nitter instances.

        Uses cache first; on miss, tries instances in order per account.
        Returns same item schema as RSSFetcher. Empty list on total failure.

        Returns:
            List of items with title, url, source, source_app, category, published.
            Sorted by published descending.
        """
        cached = self._load_cache()
        if cached is not None:
            items = cached.get("items", [])
            age = (time.time() - cached.get("saved_at", 0)) / 60
            logger.info(
                "[TWITTER] Cache hit — %s items (age: %.1f min)", len(items), age
            )
            return items

        accounts = CONFIG.get("twitter_accounts", [])
        instances = CONFIG.get("nitter_instances", [])

        if not accounts or not instances:
            logger.warning(
                "[TWITTER] Missing twitter_accounts or nitter_instances in config — skipping"
            )
            return []

        all_items: list[dict] = []

        async with aiohttp.ClientSession(
            headers=self.HEADERS, timeout=self.REQUEST_TIMEOUT
        ) as session:
            tasks = [
                self._fetch_account(session, account, instances)
                for account in accounts
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for account, result in zip(accounts, results):
            if isinstance(result, Exception):
                logger.warning(
                    "[TWITTER] @%s: fetch failed — %s",
                    account.get("handle", "?"),
                    result,
                    exc_info=False,
                )
                continue
            all_items.extend(result)

        all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

        if not all_items:
            logger.warning(
                "[TWITTER] All Nitter instances failed — skipping Twitter section"
            )

        self._save_cache(all_items)
        return all_items

    async def _fetch_account(
        self,
        session: aiohttp.ClientSession,
        account: dict,
        instances: list[str],
    ) -> list[dict]:
        """Fetch one account from Nitter; try instances in order until success."""
        handle = account.get("handle", "")
        if not handle:
            return []

        for instance in instances:
            url = f"{instance.rstrip('/')}/{handle}/rss"
            try:
                resp = await session.get(url)
                if resp.status != 200:
                    logger.debug(
                        "[TWITTER] %s/%s: HTTP %s — trying next",
                        instance,
                        handle,
                        resp.status,
                    )
                    continue

                raw_content = await resp.text()
                loop = asyncio.get_event_loop()
                parsed = await loop.run_in_executor(
                    None, feedparser.parse, raw_content
                )

                if not parsed.entries:
                    logger.debug(
                        "[TWITTER] %s/%s: 0 entries — trying next",
                        instance,
                        handle,
                    )
                    continue

                # Detect Nitter instance error responses (whitelist / block pages)
                first_title = (parsed.entries[0].get("title") or "").lower()
                _error_signals = ("whitelisted", "rate limit", "instance is down", "not found")
                if any(sig in first_title for sig in _error_signals):
                    logger.debug(
                        "[TWITTER] %s/%s: instance returned error page (%s) — trying next",
                        instance,
                        handle,
                        first_title[:40],
                    )
                    continue

                items: list[dict] = []
                for entry in parsed.entries[: self.MAX_ITEMS_PER_ACCOUNT * 2]:
                    title = (entry.get("title") or "").strip()
                    if title.startswith("RT"):
                        continue
                    url_entry = entry.get("link") or entry.get("href", "")
                    if not url_entry:
                        continue
                    items.append({
                        "title": title,
                        "url": url_entry,
                        "source": f"@{handle}",
                        "source_app": account.get("source_app", "").lower(),
                        "category": account.get("category", ""),
                        "published": _parse_published(entry),
                    })
                    if len(items) >= self.MAX_ITEMS_PER_ACCOUNT:
                        break

                logger.info(
                    "[TWITTER] @%s via %s: %s items fetched",
                    handle,
                    instance,
                    len(items),
                )
                return items

            except asyncio.TimeoutError:
                logger.debug(
                    "[TWITTER] %s/%s: timeout — trying next", instance, handle
                )
                continue
            except Exception as exc:
                logger.debug(
                    "[TWITTER] %s/%s: %s — trying next",
                    instance,
                    handle,
                    exc,
                )
                continue

        logger.warning(
            "[TWITTER] @%s: all Nitter instances failed — skipping account",
            handle,
        )
        return []

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
            logger.warning("[TWITTER] Cache save failed — %s", e)
