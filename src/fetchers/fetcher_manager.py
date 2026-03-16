from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from src.fetchers.market_fetcher import MarketFetcher
from src.fetchers.onchain_fetcher import OnChainFetcher
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.twitter_fetcher import TwitterFetcher
from src.utils.cache_manager import cache

logger = logging.getLogger(__name__)


class FetcherManager:
    """Singleton orchestrator that runs all fetchers in parallel.

    Fetcher instances are created once and reused across all calls, preserving
    each fetcher's in-memory cache between scheduler runs and manual /digest calls.
    """

    _instance: "FetcherManager | None" = None

    def __new__(cls) -> "FetcherManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._rss = RSSFetcher()
            cls._instance._twitter = TwitterFetcher()
            cls._instance._market = MarketFetcher()
            cls._instance._onchain = OnChainFetcher()
        return cls._instance

    async def fetch_all_sources(self, force: bool = False) -> dict:
        """Fetch data from all sources and return a unified snapshot.

        Args:
            force: If True, bypass the on-disk snapshot cache and re-fetch
                   from all sources. Individual fetcher caches still apply.

        Returns:
            dict with keys: rss, twitter, market, onchain, fetched_at.
        """
        if not force:
            snapshot = await cache.get_snapshot()
            if snapshot:
                age = cache.get_age_minutes("full_snapshot") or 0
                logger.info("[FETCH] Snapshot cache hit (age: %.1fmin)", age)
                return snapshot

        start = time.time()
        logger.info("[FETCH] Starting parallel fetch of all sources...")

        results = await asyncio.gather(
            asyncio.wait_for(self._rss.fetch_all(),     timeout=15.0),
            asyncio.wait_for(self._twitter.fetch_all(), timeout=15.0),
            asyncio.wait_for(self._market.fetch(),      timeout=10.0),
            asyncio.wait_for(self._onchain.fetch(),     timeout=10.0),
            return_exceptions=True,
        )
        rss_result, twitter_result, market_result, onchain_result = results

        rss_items = rss_result if isinstance(rss_result, list) else []
        twitter_items = twitter_result if isinstance(twitter_result, list) else []
        market_data = market_result if isinstance(market_result, dict) else {}
        onchain_data = onchain_result if isinstance(onchain_result, dict) else {}

        for name, result in zip(["rss", "twitter", "market", "onchain"], results):
            if isinstance(result, Exception):
                logger.warning("[FETCH] %s fetcher failed: %s", name, result)

        elapsed = time.time() - start
        snapshot = {
            "rss": rss_items,
            "twitter": twitter_items,
            "market": market_data,
            "onchain": onchain_data,
            "fetched_at": datetime.utcnow().isoformat(),
        }

        market_ok = "OK" if market_data else "FAIL"
        onchain_ok = "OK" if onchain_data else "FAIL"
        logger.info(
            f"[FETCH] RSS: {len(rss_items)} | Twitter: {len(twitter_items)} "
            f"| Market: {market_ok} | OnChain: {onchain_ok} "
            f"| {elapsed:.1f}s"
        )

        await cache.set_snapshot(snapshot)
        return snapshot

    async def get_cached_snapshot(self) -> dict | None:
        """Return the on-disk snapshot without making any network requests.

        Returns:
            Snapshot dict if a valid cache exists, else None.
        """
        return await cache.get_snapshot()


# Module-level singleton — import this directly, never instantiate FetcherManager elsewhere
fetcher_manager = FetcherManager()
