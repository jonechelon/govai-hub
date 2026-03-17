from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.exceptions import BlockNotFound, ContractLogicError

from src.utils.env_validator import get_env_or_fail

logger = logging.getLogger(__name__)


# ⚠️ Confirm this address at https://celoscan.io before production use
GOVERNANCE_ADDRESS = Web3.to_checksum_address(
    "0xD533Ca259b330c7A88f74E000a3FaEa2d63B7972"
)

GOVERNANCE_ABI_MINIMAL: list[dict[str, Any]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": True, "name": "proposer", "type": "address"},
            {"indexed": False, "name": "deposit", "type": "uint256"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "transactionCount", "type": "uint256"},
            {"indexed": False, "name": "descriptionUrl", "type": "string"},
        ],
        "name": "ProposalQueued",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [{"indexed": True, "name": "proposalId", "type": "uint256"}],
        "name": "ProposalExecuted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [{"indexed": True, "name": "proposalId", "type": "uint256"}],
        "name": "ProposalDequeued",
        "type": "event",
    },
]

CACHE_PATH = Path("data/cache/governance_last_block.json")
BLOCKS_LOOKBACK = 1800  # ~5h on Celo (~2s/block)
RPC_TIMEOUT = 10  # seconds
RETRY_DELAYS = [2, 4, 8]  # backoff in seconds


class GovernanceFetcher:
    """Fetches new governance proposals from the Celo Governance contract."""

    def __init__(self) -> None:
        """Initialize Web3 providers for primary and optional fallback RPC."""
        primary_rpc = get_env_or_fail("CELO_RPC_URL")
        fallback_rpc = None
        try:
            # Optional env var; do not fail if missing
            fallback_rpc = get_env_or_fail("CELO_RPC_FALLBACK_URL")
        except ValueError:
            fallback_rpc = None

        self._w3_primary = Web3(
            Web3.HTTPProvider(primary_rpc, request_kwargs={"timeout": RPC_TIMEOUT})
        )
        self._w3_fallback = (
            Web3(
                Web3.HTTPProvider(
                    fallback_rpc, request_kwargs={"timeout": RPC_TIMEOUT}
                )
            )
            if fallback_rpc
            else None
        )

    def fetch_new_proposals(self, from_block: int | None = None) -> list[dict]:
        """Fetch newly queued governance proposals from Celo.

        This method is synchronous; callers should run it in an executor
        when using from async contexts.

        Args:
            from_block: Optional starting block number. When None, uses
                cached last processed block if available, or current block
                minus BLOCKS_LOOKBACK.

        Returns:
            List of proposal dicts derived from ProposalQueued events.
            Never raises; returns [] on failure.
        """
        try:
            w3 = self._w3_primary
            current_block = self._get_current_block_with_retry(w3)
            if current_block is None or current_block <= 0:
                return []

            start_block = self._resolve_from_block(from_block, current_block)

            contract = w3.eth.contract(
                address=GOVERNANCE_ADDRESS, abi=GOVERNANCE_ABI_MINIMAL
            )

            events = self._get_proposal_events_with_retry(
                contract, start_block, "latest"
            )
            proposals = [self._event_to_dict(e) for e in events]

            self._save_cache(current_block)
            logger.info(
                "[GOVERNANCE] Scanned blocks #%s→#%s | found: %s proposals",
                f"{start_block:,}",
                "latest",
                len(proposals),
            )
            return proposals

        except BlockNotFound as exc:
            logger.warning("[GOVERNANCE] Block not found: %s", exc)
        except ContractLogicError as exc:
            logger.error("[GOVERNANCE] Contract logic error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GOVERNANCE] Unexpected error: %s", exc)

        return []

    def _get_current_block_with_retry(self, w3: Web3) -> int | None:
        """Get current block number with retry and fallback provider."""
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            try:
                return int(w3.eth.block_number)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[GOVERNANCE] block_number attempt %s failed: %s", attempt, exc
                )
                if attempt >= len(RETRY_DELAYS):
                    break
                time.sleep(delay)
                if attempt >= 1 and self._w3_fallback is not None:
                    w3 = self._w3_fallback
                    logger.warning(
                        "[GOVERNANCE] Switching to fallback RPC for block_number"
                    )
        return None

    def _resolve_from_block(self, from_block: int | None, current_block: int) -> int:
        """Decide starting block based on input, cache, or lookback."""
        if from_block is not None:
            return max(0, int(from_block))

        cached = self._load_cache()
        if cached is not None:
            last_block = cached.get("last_block")
            if isinstance(last_block, int) and last_block >= 0:
                return last_block + 1

        return max(0, current_block - BLOCKS_LOOKBACK)

    def _get_proposal_events_with_retry(
        self,
        contract: Any,
        from_block: int,
        to_block: int | str,
    ) -> list[Any]:
        """Fetch ProposalQueued logs with retry and optional fallback provider."""
        events: list[Any] = []
        w3 = self._w3_primary

        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            try:
                event_filter = contract.events.ProposalQueued
                events = event_filter.get_logs(
                    fromBlock=from_block,
                    toBlock=to_block,
                )
                return events
            except (BlockNotFound, ContractLogicError):
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[GOVERNANCE] get_logs attempt %s failed: %s", attempt, exc
                )
                if attempt >= len(RETRY_DELAYS):
                    break
                time.sleep(delay)
                if attempt >= 2 and self._w3_fallback is not None:
                    w3 = self._w3_fallback
                    logger.warning(
                        "[GOVERNANCE] Switching to fallback RPC for get_logs"
                    )
                    contract = w3.eth.contract(
                        address=GOVERNANCE_ADDRESS, abi=GOVERNANCE_ABI_MINIMAL
                    )

        return events

    def _event_to_dict(self, event: Any) -> dict:
        """Convert a ProposalQueued event into a normalized dict."""
        args = event.args
        queued_at = datetime.utcfromtimestamp(int(args.timestamp)).replace(
            tzinfo=timezone.utc
        )
        return {
            "proposal_id": int(args.proposalId),
            "proposer": Web3.to_checksum_address(str(args.proposer)),
            "description_url": str(args.descriptionUrl),
            "deposit": float(args.deposit) / 1e18,
            "queued_at": queued_at,
            "block_number": int(event.blockNumber),
            "tx_hash": event.transactionHash.hex(),
            "event_type": "queued",
        }

    def _load_cache(self) -> dict | None:
        """Load last processed block from cache file."""
        if not CACHE_PATH.exists():
            return None
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def _save_cache(self, current_block: int) -> None:
        """Persist last processed block to cache file."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {"last_block": int(current_block)}
            CACHE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[GOVERNANCE] Cache save failed: %s", exc)

