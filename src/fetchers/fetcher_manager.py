from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from src.fetchers.market_fetcher import MarketFetcher
from src.fetchers.onchain_fetcher import OnChainFetcher
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.twitter_fetcher import TwitterFetcher

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = Path("data/cache/full_snapshot.json")
SNAPSHOT_TTL_MIN = 30


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
            cached = self._load_snapshot()
            if cached:
                age = (time.time() - cached["_saved_at"]) / 60
                logger.info(f"[FETCH] Snapshot cache hit (age: {age:.1f}min)")
                return {k: v for k, v in cached.items() if k != "_saved_at"}

        start = time.time()

        rss_result, twitter_result, market_result, onchain_result = await asyncio.gather(
            self._rss.fetch_all(),
            self._twitter.fetch_all(),
            self._market.fetch(),
            self._onchain.fetch(),
            return_exceptions=True,
        )

        rss_items = rss_result if isinstance(rss_result, list) else []
        twitter_items = twitter_result if isinstance(twitter_result, list) else []
        market_data = market_result if isinstance(market_result, dict) else {}
        onchain_data = onchain_result if isinstance(onchain_result, dict) else {}

        if isinstance(rss_result, Exception):
            logger.error(f"[FETCH] RSS failed: {rss_result}")
        if isinstance(twitter_result, Exception):
            logger.error(f"[FETCH] Twitter failed: {twitter_result}")
        if isinstance(market_result, Exception):
            logger.error(f"[FETCH] Market failed: {market_result}")
        if isinstance(onchain_result, Exception):
            logger.error(f"[FETCH] OnChain failed: {onchain_result}")

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

        self._save_snapshot(snapshot)
        return snapshot

    def get_cached_snapshot(self) -> dict | None:
        """Return the on-disk snapshot without making any network requests.

        Returns:
            Snapshot dict (without _saved_at) if a valid cache exists, else None.
        """
        cached = self._load_snapshot()
        if cached:
            return {k: v for k, v in cached.items() if k != "_saved_at"}
        return None

    def _load_snapshot(self) -> dict | None:
        """Load snapshot from disk if it exists and has not expired.

        Returns:
            Raw dict including _saved_at if valid, else None.
        """
        if not SNAPSHOT_FILE.exists():
            return None

        try:
            data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[FETCH] Could not read snapshot file: {e}")
            return None

        saved_at = data.get("_saved_at")
        if saved_at is None:
            return None

        age_min = (time.time() - saved_at) / 60
        if age_min > SNAPSHOT_TTL_MIN:
            return None

        return data

    def _save_snapshot(self, snapshot: dict) -> None:
        """Persist snapshot to disk with an internal _saved_at timestamp.

        Args:
            snapshot: The snapshot dict to save (without _saved_at).
        """
        try:
            SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            to_save = {**snapshot, "_saved_at": time.time()}
            SNAPSHOT_FILE.write_text(
                json.dumps(to_save, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"[FETCH] Failed to save snapshot: {e}")


# Module-level singleton — import this directly, never instantiate FetcherManager elsewhere
fetcher_manager = FetcherManager()
