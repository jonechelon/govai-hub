# src/fetchers/onchain_fetcher.py
# Celo GovAI Hub — On-chain fetcher for Celo stablecoin supplies and block number (P14)

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from web3 import Web3

from src.utils.config_loader import CONFIG
from src.utils.paths import ONCHAIN_CACHE_PATH

logger = logging.getLogger(__name__)

# Phase 7 — multi-network RPC resolver with mainnet fallback
def get_w3(network: str = "mainnet") -> Web3:
    """Return a Web3 instance for the given Celo network.

    Falls back to CELO_RPC_URL (mainnet) if the network-specific env var is unset.
    Valid values: 'mainnet', 'alfajores', 'sepolia'.
    """
    urls = {
        "mainnet": CONFIG.get("celo_rpc", {}).get("primary")
        or os.getenv("CELO_RPC_URL"),
        "alfajores": os.getenv("CELO_ALFAJORES_RPC_URL"),
        "sepolia": os.getenv("CELO_SEPOLIA_RPC_URL"),
    }
    url = urls.get(network) or os.getenv("CELO_RPC_URL")
    if network in urls and not urls[network]:
        print(
            f"[WARN] {network.upper()}_RPC_URL not set, falling back to mainnet"
        )
    if not url:
        url = CONFIG.get("celo_rpc", {}).get(
            "fallback", "https://rpc.ankr.com/celo"
        )
    return Web3(
        Web3.HTTPProvider(url, request_kwargs={"timeout": 10})
    )


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

# StakedCelo token ERC-20 — tradable on DEX; balanceOf for user holdings
STCELO_TOKEN_ADDRESS = "0xC668583dcbDc9ae6FA3CE46462758188adfdfC24"

# StakedCelo Manager proxy — exposes toCelo(uint256) for CELO per 1 stCELO (Celoscan Read Contract).
# The Account proxy (0x4aAD04D41FD7fd495503731C5a2579e19054C432) does not expose toCelo on the
# proxy (eth_call reverts); the documented rate view is on the Manager, not the ERC-20 token.
STCELO_MANAGER_ADDRESS = "0x0239b96D10a434a56CC9E09383077A0490cF9398"

# Minimal ABI: ERC-20 balanceOf
STCELO_TOKEN_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Minimal ABI: Manager exchange rate
STCELO_MANAGER_ABI = [
    {
        "inputs": [{"name": "stCeloAmount", "type": "uint256"}],
        "name": "toCelo",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


async def get_stcelo_data(
    wallet_address: str | None = None, network: str = "mainnet"
) -> dict:
    """
    Returns stCELO balance + exchange rate from Celo Mainnet.
    Uses ERC-20 token at STCELO_TOKEN_ADDRESS for balanceOf; Manager toCelo for rate.
    Falls back gracefully on any RPC or contract error (no stack traces to callers).
    """
    result = {
        "stcelo_balance": 0.0,
        "exchange_rate": None,  # CELO per 1 stCELO (from Manager.toCelo(1e18))
        "apy": None,
        "error": None,
    }
    rpc_timeout = OnChainFetcher.RPC_TIMEOUT
    w3 = get_w3(network)
    if not w3.is_connected():
        fallback_url = CONFIG.get("celo_rpc", {}).get(
            "fallback", "https://rpc.ankr.com/celo"
        )
        w3 = Web3(
            Web3.HTTPProvider(
                fallback_url, request_kwargs={"timeout": rpc_timeout}
            )
        )
    if not w3.is_connected():
        logger.warning("[STCELO] RPC unreachable — exchange rate unavailable")
        result["error"] = "rate_unavailable"
        return result

    loop = asyncio.get_event_loop()
    try:
        manager = w3.eth.contract(
            address=Web3.to_checksum_address(STCELO_MANAGER_ADDRESS),
            abi=STCELO_MANAGER_ABI,
        )
        # toCelo(1e18) = CELO wei value of exactly 1 stCELO (see StakedCelo Manager on Celoscan)
        one_stcelo_wei = 10**18

        def _call_to_celo() -> int:
            return manager.functions.toCelo(one_stcelo_wei).call()

        celo_per_stcelo_wei = await loop.run_in_executor(None, _call_to_celo)
        result["exchange_rate"] = round(celo_per_stcelo_wei / 1e18, 6)
    except Exception as exc:
        logger.warning(
            "[STCELO] toCelo() failed — %s: %s",
            type(exc).__name__,
            exc,
        )
        result["exchange_rate"] = None
        result["error"] = "rate_unavailable"

    if wallet_address:
        try:
            token = w3.eth.contract(
                address=Web3.to_checksum_address(STCELO_TOKEN_ADDRESS),
                abi=STCELO_TOKEN_ABI,
            )

            def _call_balance() -> int:
                return token.functions.balanceOf(
                    Web3.to_checksum_address(wallet_address)
                ).call()

            raw_balance = await loop.run_in_executor(None, _call_balance)
            result["stcelo_balance"] = raw_balance / 1e18
        except Exception as exc:
            logger.warning(
                "[STCELO] balanceOf failed — %s: %s",
                type(exc).__name__,
                exc,
            )
            result["stcelo_balance"] = 0.0

    return result


class OnChainFetcher:
    """Fetches on-chain data from Celo (block number, cUSD/cEUR/cREAL supplies) via web3.py."""

    CACHE_FILE = ONCHAIN_CACHE_PATH
    CACHE_TTL_MINUTES = 5  # shorter TTL — block data changes every ~5s
    RPC_TIMEOUT = 10  # seconds

    async def fetch(self, network: str = "mainnet") -> dict:
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

        w3 = get_w3(network)

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


# Minimal ERC-20 ABI — balanceOf only
_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

_TOKEN_CONTRACTS = {
    "USDm": "0x765DE816845861e75A25fCA122bb6898B8B1282a",
    "USDC": "0xcebA9300f2b948710d2653dD7B07f33A8B32118C",
}

_TOKEN_DECIMALS = {
    "CELO": 18,
    "USDm": 18,
    "USDC": 6,
}


async def fetch_treasury_balance(token: str, network: str = "mainnet") -> dict:
    """
    Reads the treasury balance for the given token via web3.py.
    Runs the synchronous web3 call in loop.run_in_executor to avoid
    blocking the async event loop.

    Returns on success:  {"balance": "123.45", "token": token, "error": None}
    On missing env / RPC errors: {"balance": None, "token": token, "error": "Balance unavailable"}
    On unsupported token: {"balance": None, "token": token, "error": "Unsupported token"}
    """
    rpc_url = os.getenv("CELO_RPC_URL", "").strip()
    treasury = os.getenv("TREASURY_ADDRESS", "").strip()

    if network == "mainnet":
        if not rpc_url or not treasury:
            return {"balance": None, "token": token, "error": "Balance unavailable"}
    elif not treasury:
        return {"balance": None, "token": token, "error": "Balance unavailable"}

    if token != "CELO" and token not in _TOKEN_CONTRACTS:
        return {"balance": None, "token": token, "error": "Unsupported token"}

    def _sync_fetch() -> str:
        w3 = get_w3(network)
        addr = Web3.to_checksum_address(treasury)
        if token == "CELO":
            raw = w3.eth.get_balance(addr)
        else:
            contract_addr = Web3.to_checksum_address(_TOKEN_CONTRACTS[token])
            contract = w3.eth.contract(address=contract_addr, abi=_ERC20_ABI)
            raw = contract.functions.balanceOf(addr).call()
        decimals = _TOKEN_DECIMALS[token]
        return str(round(raw / (10**decimals), 6))

    try:
        loop = asyncio.get_event_loop()
        balance = await loop.run_in_executor(None, _sync_fetch)
        return {"balance": balance, "token": token, "error": None}
    except Exception:
        return {"balance": None, "token": token, "error": "Balance unavailable"}
