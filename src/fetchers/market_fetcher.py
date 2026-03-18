# src/fetchers/market_fetcher.py
# Celo GovAI Hub — Market fetcher for CELO via CoinGecko and DeFi Llama (P13)

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
import aiohttp

from src.utils.paths import MARKET_CACHE_PATH

logger = logging.getLogger(__name__)


class MarketFetcher:
    """Fetches CELO market data from CoinGecko and TVL from DeFi Llama."""

    CACHE_FILE = MARKET_CACHE_PATH
    CACHE_TTL_MINUTES = 30
    REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Celo GovAI Hub/1.1)"}
    MAX_RETRIES = 3
    RETRY_BACKOFF = (1, 2, 4)  # seconds between retries

    COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/celo"
    COINGECKO_PARAMS = {
        "localization": "false",
        "tickers": "false",
        "community_data": "false",
        "developer_data": "false",
        "sparkline": "false",
    }
    # /v2/chains is the free-tier endpoint; /tvl/{chain} was moved to pro-api (paid)
    DEFILLAMA_URL = "https://api.llama.fi/v2/chains"

    async def fetch(self) -> dict:
        """Fetch market data from CoinGecko and DeFi Llama, with cache and fallback.

        Returns:
            Dict with price, pct_24h, market_cap, volume, tvl, fetched_at.
            Missing values are 0.0 on partial failure. Never raises.
        """
        cached = self._load_cache()
        if cached is not None:
            age = (time.time() - cached.get("saved_at", 0)) / 60
            logger.info("[MARKET] Cache hit (age: %.1f min)", age)
            return cached["data"]

        cg_result: dict | Exception = {}
        llama_result: float | Exception = 0.0

        async with aiohttp.ClientSession(
            headers=self.HEADERS, timeout=self.REQUEST_TIMEOUT
        ) as session:
            cg_task = self._fetch_coingecko(session)
            llama_task = self._fetch_defillama(session)
            cg_result, llama_result = await asyncio.gather(
                cg_task, llama_task, return_exceptions=True
            )

        if isinstance(cg_result, Exception):
            logger.warning("[MARKET] CoinGecko failed — %s", cg_result)
            cg_result = {}
        if isinstance(llama_result, Exception):
            logger.warning("[MARKET] DeFi Llama failed — %s", llama_result)
            llama_result = 0.0

        data = {
            "price": float(cg_result.get("price", 0.0)),
            "pct_24h": float(cg_result.get("pct_24h", 0.0)),
            "market_cap": float(cg_result.get("market_cap", 0.0)),
            "volume": float(cg_result.get("volume", 0.0)),
            "tvl": float(llama_result) if isinstance(llama_result, (int, float)) else 0.0,
            "fetched_at": datetime.utcnow().isoformat(),
        }

        if data["price"] == 0.0 and data["tvl"] == 0.0:
            stale = self._load_cache(ignore_ttl=True)
            if stale:
                logger.warning("[MARKET] Both sources failed — using stale cache")
                return stale["data"]

        self._save_cache(data)
        logger.info(
            "[MARKET] CELO: $%.4f (%+.2f%%) | TVL: $%s",
            data["price"],
            data["pct_24h"],
            f"{data['tvl']:,.0f}",
        )
        return data

    async def _fetch_coingecko(
        self, session: aiohttp.ClientSession
    ) -> dict:
        """Fetch CELO price data from CoinGecko with retries and backoff."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await session.get(
                    self.COINGECKO_URL, params=self.COINGECKO_PARAMS
                )

                if resp.status == 429:
                    wait = self.RETRY_BACKOFF[
                        min(attempt, len(self.RETRY_BACKOFF) - 1)
                    ]
                    logger.warning(
                        "[MARKET] CoinGecko rate limited — retrying in %ds", wait
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status != 200:
                    raise ValueError(f"CoinGecko HTTP {resp.status}")

                raw = await resp.json()
                market = raw.get("market_data") or {}
                current = market.get("current_price") or {}
                mcap = market.get("market_cap") or {}
                vol = market.get("total_volume") or {}

                return {
                    "price": float(current.get("usd", 0.0)),
                    "pct_24h": float(
                        market.get("price_change_percentage_24h") or 0.0
                    ),
                    "market_cap": float(mcap.get("usd", 0.0)),
                    "volume": float(vol.get("usd", 0.0)),
                }

            except (
                asyncio.TimeoutError,
                aiohttp.ClientError,
                ValueError,
            ) as exc:
                wait = self.RETRY_BACKOFF[
                    min(attempt, len(self.RETRY_BACKOFF) - 1)
                ]
                logger.warning(
                    "[MARKET] CoinGecko attempt %d/%d failed: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    exc,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"CoinGecko failed after {self.MAX_RETRIES} attempts"
        )

    async def _fetch_defillama(
        self, session: aiohttp.ClientSession
    ) -> float:
        """Fetch Celo chain TVL from DeFi Llama /v2/chains (free tier).

        Returns TVL as float in USD. Raises RuntimeError after all retries fail.

        Uses content_type=None because DeFi Llama sometimes returns
        Content-Type: text/plain for a JSON array body.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await session.get(self.DEFILLAMA_URL)

                if resp.status != 200:
                    raise ValueError(f"DeFi Llama HTTP {resp.status}")

                chains: list[dict] = await resp.json(content_type=None)

                celo = next(
                    (c for c in chains if c.get("name", "").lower() == "celo"),
                    None,
                )

                if celo is None:
                    raise ValueError("Celo chain not found in DeFi Llama response")

                tvl = float(celo.get("tvl", 0.0))
                logger.debug("[MARKET] DeFi Llama: Celo TVL = $%s", f"{tvl:,.0f}")
                return tvl

            except (
                asyncio.TimeoutError,
                aiohttp.ClientError,
                ValueError,
            ) as exc:
                wait = self.RETRY_BACKOFF[
                    min(attempt, len(self.RETRY_BACKOFF) - 1)
                ]
                logger.warning(
                    "[MARKET] DeFi Llama attempt %d/%d failed: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    exc,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"DeFi Llama failed after {self.MAX_RETRIES} attempts"
        )

    def _load_cache(self, ignore_ttl: bool = False) -> dict | None:
        """Load cache from disk if present; optionally ignore TTL for stale fallback."""
        if not self.CACHE_FILE.exists():
            return None
        try:
            data = json.loads(self.CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not ignore_ttl:
            saved_at = data.get("saved_at", 0)
            age_minutes = (time.time() - saved_at) / 60
            if age_minutes > self.CACHE_TTL_MINUTES:
                return None
        return data

    def _save_cache(self, data: dict) -> None:
        """Persist market data to cache file. Logs warning on failure, does not raise."""
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": time.time(), "data": data}
            self.CACHE_FILE.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("[MARKET] Cache save failed — %s", e)
