# src/fetchers/onchain_fetcher.py
# Up-to-Celo — On-chain fetcher for Celo stablecoin supplies and block number (P14)

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from web3 import Web3

from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail
from src.utils.paths import ONCHAIN_CACHE_PATH

logger = logging.getLogger(__name__)

# Minimal ABI for ERC20 totalSupply() — do not add other methods (MismatchedABI risk)
ERC20_TOTAL_SUPPLY_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]


class OnChainFetcher:
    """Fetches on-chain data from Celo (block number, cUSD/cEUR/cREAL supplies) via web3.py."""

    CACHE_FILE = ONCHAIN_CACHE_PATH
    CACHE_TTL_MINUTES = 5  # shorter TTL — block data changes every ~5s
    RPC_TIMEOUT = 10  # seconds

    async def fetch(self) -> dict:
        """Fetch block number and stablecoin supplies from Celo RPC, with cache and fallback.

        Returns:
            Dict with block_number, cusd_supply, ceur_supply, creal_supply, fetched_at.
            Supplies are in tokens (not wei). Never raises; uses 0/0.0 on partial failure.
        """
        cached = self._load_cache()
        if cached is not None:
            age = (time.time() - cached.get("saved_at", 0)) / 60
            logger.info("[ONCHAIN] Cache hit (age: %.1f min)", age)
            return cached["data"]

        rpc_url = (
            CONFIG.get("celo_rpc", {}).get("primary")
            or get_env_or_fail("CELO_RPC_URL")
        )
        w3 = Web3(
            Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": self.RPC_TIMEOUT})
        )

        if not w3.is_connected():
            fallback_url = CONFIG.get("celo_rpc", {}).get(
                "fallback", "https://rpc.ankr.com/celo"
            )
            logger.warning("[ONCHAIN] Primary RPC failed — switching to fallback")
            w3 = Web3(
                Web3.HTTPProvider(
                    fallback_url, request_kwargs={"timeout": self.RPC_TIMEOUT}
                )
            )
            if not w3.is_connected():
                logger.error("[ONCHAIN] Fallback RPC also failed — using stale cache")
                stale = self._load_cache(ignore_ttl=True)
                if stale:
                    return stale["data"]
                return self._empty_result()

        loop = asyncio.get_event_loop()
        block_task = loop.run_in_executor(None, lambda: w3.eth.block_number)
        cusd_task = loop.run_in_executor(None, lambda: self._get_supply(w3, "cusd"))
        ceur_task = loop.run_in_executor(None, lambda: self._get_supply(w3, "ceur"))
        creal_task = loop.run_in_executor(None, lambda: self._get_supply(w3, "creal"))

        results = await asyncio.gather(
            block_task, cusd_task, ceur_task, creal_task, return_exceptions=True
        )
        block_number, cusd_supply, ceur_supply, creal_supply = results

        if isinstance(block_number, Exception):
            logger.warning("[ONCHAIN] block_number failed: %s", block_number)
            block_number = 0
        if isinstance(cusd_supply, Exception):
            logger.warning("[ONCHAIN] cUSD supply failed: %s", cusd_supply)
            cusd_supply = 0.0
        if isinstance(ceur_supply, Exception):
            logger.warning("[ONCHAIN] cEUR supply failed: %s", ceur_supply)
            ceur_supply = 0.0
        if isinstance(creal_supply, Exception):
            logger.warning("[ONCHAIN] cREAL supply failed: %s", creal_supply)
            creal_supply = 0.0

        data = {
            "block_number": block_number if isinstance(block_number, int) else 0,
            "cusd_supply": cusd_supply if isinstance(cusd_supply, (int, float)) else 0.0,
            "ceur_supply": ceur_supply if isinstance(ceur_supply, (int, float)) else 0.0,
            "creal_supply": creal_supply if isinstance(creal_supply, (int, float)) else 0.0,
            "fetched_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            "[ONCHAIN] Block: #%s | cUSD: %.1fM",
            f"{data['block_number']:,}",
            data["cusd_supply"] / 1_000_000.0,
        )

        self._save_cache(data)
        return data

    def _get_supply(self, w3: Web3, token: str) -> float:
        """Get total supply of a Celo stablecoin (sync — called via run_in_executor).

        Args:
            w3: Web3 instance connected to Celo RPC.
            token: Key in config celo_contracts: 'cusd', 'ceur', or 'creal'.

        Returns:
            Total supply in tokens (not wei).

        Raises:
            RuntimeError: On contract call failure (caller catches via return_exceptions).
        """
        try:
            raw_address = CONFIG["celo_contracts"][token]
            address = Web3.to_checksum_address(raw_address)
            contract = w3.eth.contract(address=address, abi=ERC20_TOTAL_SUPPLY_ABI)
            supply_wei = contract.functions.totalSupply().call()
            return float(supply_wei) / 1e18
        except Exception as exc:
            raise RuntimeError(f"totalSupply() failed for {token}: {exc}") from exc

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
        """Persist on-chain data to cache file. Logs warning on failure, does not raise."""
        try:
            self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": time.time(), "data": data}
            self.CACHE_FILE.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("[ONCHAIN] Cache save failed — %s", e)

    def _empty_result(self) -> dict:
        """Return a valid result dict with zeros when no RPC and no cache."""
        return {
            "block_number": 0,
            "cusd_supply": 0.0,
            "ceur_supply": 0.0,
            "creal_supply": 0.0,
            "fetched_at": datetime.utcnow().isoformat(),
        }
