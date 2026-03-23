from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
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
    # View functions for proposal list status (celocli governance:list equivalent).
    # Note: names match the on-chain Governance contract interface on Celo.
    {
        "constant": True,
        "inputs": [],
        "name": "getQueue",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getDequeue",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    # View function to resolve the true proposal stage.
    # Returns an enum-like uint8 (implementation-dependent).
    {
        "constant": True,
        "inputs": [
            {"internalType": "uint256", "name": "proposalId", "type": "uint256"}
        ],
        "name": "getProposalStage",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    # View function: reads proposal data directly from contract storage.
    # Returns (proposer, deposit, timestamp, transactionCount, descriptionUrl,
    #          networkWeight, approved).
    # Reverts with ContractLogicError when proposalId does not exist.
    {
        "constant": True,
        "inputs": [
            {"internalType": "uint256", "name": "proposalId", "type": "uint256"}
        ],
        "name": "getProposal",
        "outputs": [
            {"internalType": "address", "name": "proposer", "type": "address"},
            {"internalType": "uint256", "name": "deposit", "type": "uint256"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
            {"internalType": "uint256", "name": "transactionCount", "type": "uint256"},
            {"internalType": "string", "name": "descriptionUrl", "type": "string"},
            {"internalType": "uint256", "name": "networkWeight", "type": "uint256"},
            {"internalType": "bool", "name": "approved", "type": "bool"},
        ],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]

BLOCKS_LOOKBACK = 1800  # ~5h on Celo (~2s/block)
RPC_TIMEOUT = 10  # seconds
RETRY_DELAYS = [2, 4, 8]  # backoff in seconds


def _fetch_proposal_url_sync(proposal_id: int) -> str | None:
    """Read a proposal's descriptionUrl directly from the Celo Governance contract.

    This is a synchronous helper meant to be called via asyncio.to_thread.
    It calls getProposal(proposalId) and extracts the descriptionUrl field.
    Returns None when the proposal does not exist (contract reverts) or on any
    RPC error, so callers can safely treat None as "not found".

    Args:
        proposal_id: On-chain integer identifier of the governance proposal.

    Returns:
        The descriptionUrl string if found and non-empty, otherwise None.
    """
    try:
        rpc_url = get_env_or_fail("CELO_RPC_URL")
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": RPC_TIMEOUT}))
        contract = w3.eth.contract(
            address=GOVERNANCE_ADDRESS, abi=GOVERNANCE_ABI_MINIMAL
        )
        # Returns tuple: (proposer, deposit, timestamp, transactionCount,
        #                 descriptionUrl, networkWeight, approved)
        result = contract.functions.getProposal(proposal_id).call()
        description_url: str = result[4]  # index 4 = descriptionUrl
        if description_url and description_url.strip():
            return description_url.strip()
        return None
    except ContractLogicError:
        # Contract reverts when proposalId does not exist on-chain.
        logger.info(
            "[GOVERNANCE] getProposal reverted — proposal #%s not found on-chain",
            proposal_id,
        )
        return None
    except Exception as exc:
        logger.warning(
            "[GOVERNANCE] getProposal RPC error | proposal_id=%s | error=%s",
            proposal_id,
            exc,
        )
        return None


async def get_proposal_url_onchain(proposal_id: int) -> str | None:
    """Async wrapper: fetch proposal descriptionUrl from the Celo Governance contract.

    Runs the synchronous web3.py call in a thread pool so it never blocks the
    event loop. Returns None when the proposal does not exist or the RPC fails.

    Args:
        proposal_id: On-chain integer identifier of the governance proposal.

    Returns:
        The descriptionUrl string if found and non-empty, otherwise None.
    """
    return await asyncio.to_thread(_fetch_proposal_url_sync, proposal_id)


def _get_active_proposals_onchain_sync(w3: Web3, contract_address: str) -> dict[str, list[int]]:
    """Return queued and dequeued proposal IDs from the Governance contract (sync helper).

    Args:
        w3: Initialized Web3 instance (HTTPProvider recommended).
        contract_address: Governance contract address (0x...).

    Returns:
        Dict with integer proposal IDs grouped by status:
        {"queued": [...], "dequeued": [...]}.
        Returns empty lists on any failure.
    """
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=GOVERNANCE_ABI_MINIMAL,
        )
        queued_raw = contract.functions.getQueue().call()
        dequeued_raw = contract.functions.getDequeue().call()

        queued = [int(x) for x in (queued_raw or [])]
        dequeued = [int(x) for x in (dequeued_raw or [])]
        return {"queued": queued, "dequeued": dequeued}
    except ContractLogicError as exc:
        logger.warning("[GOVERNANCE] getQueue/getDequeue reverted | error=%s", exc)
        return {"queued": [], "dequeued": []}
    except Exception as exc:
        logger.warning("[GOVERNANCE] Active proposals RPC error | error=%s", exc)
        return {"queued": [], "dequeued": []}


def _get_proposal_stage_sync(w3: Web3, contract_address: str, proposal_id: int) -> int | None:
    """Fetch the proposal stage from the Governance contract (sync helper).

    Args:
        w3: Initialized Web3 instance.
        contract_address: Governance contract address (0x...).
        proposal_id: Proposal id to query.

    Returns:
        Integer stage value, or None if the call fails / proposal does not exist.
    """
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=GOVERNANCE_ABI_MINIMAL,
        )
        stage = contract.functions.getProposalStage(int(proposal_id)).call()
        return int(stage)
    except ContractLogicError:
        return None
    except Exception as exc:
        logger.debug(
            "[GOVERNANCE] getProposalStage failed | proposal_id=%s | error=%s",
            proposal_id,
            exc,
        )
        return None


def _classify_proposal_status(stage: int | None) -> str | None:
    """Classify a proposal into active governance UX buckets.

    Mapping (contract enum -> active-only UX):
      - 1 = Queued
      - 2 = Approval (treated as Queued)
      - 3 = Active (Voting)
      - 0 / 4 / None = ignored (history / not actionable)
    """
    if stage in {1, 2}:
        return "Queued"
    if stage == 3:
        return "Active"
    # Ignore history / not actionable proposals.
    return None


async def get_active_proposals_onchain(w3: Web3, contract_address: str) -> dict[str, list[int]]:
    """Get an active-only governance dashboard from on-chain state.

    Steps:
      1. Read raw arrays from getQueue() and getDequeue().
      2. Remove zeros (0) and deduplicate proposal ids.
      3. Resolve the true on-chain status for each proposal id using
         `getProposalStage(id)`.
      4. Categorize into UX buckets:
         - Queued
         - Active
      5. Sort ids desc (most recent → oldest). Ignore stage 0 / 4 / None.

    Args:
        w3: Initialized Web3 instance (HTTPProvider recommended).
        contract_address: Governance contract address (0x...).

    Returns:
        Dict with integer proposal IDs grouped by status:
        {"Queued": [...], "Active": [...]}.
        Returns empty lists on failure.
    """
    try:
        raw = await asyncio.to_thread(
            _get_active_proposals_onchain_sync, w3, contract_address
        )
        queued_raw = raw.get("queued", [])
        dequeued_raw = raw.get("dequeued", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GOVERNANCE] get_active_proposals_onchain failed to read queues | error=%s",
            exc,
        )
        return {
            "Queued": [],
            "Active": [],
        }

    # Merge + clean raw arrays: remove zeros and duplicates.
    queued_set = {int(x) for x in queued_raw if int(x) != 0}
    dequeued_set = {int(x) for x in dequeued_raw if int(x) != 0}
    all_ids = sorted(queued_set | dequeued_set, reverse=True)

    if not all_ids:
        return {
            "Queued": [],
            "Active": [],
        }

    def _get_stage_sync(
        w3_sync: Web3, contract_addr: str, pid: int
    ) -> tuple[int, int | None]:
        """Sync helper for getProposalStage.

        Returns (proposal_id, stage).
        On any revert/error, stage is returned as None.
        """
        try:
            w3_local = w3_sync
            contract = w3_local.eth.contract(
                address=Web3.to_checksum_address(contract_addr),
                abi=GOVERNANCE_ABI_MINIMAL,
            )
            stage = contract.functions.getProposalStage(int(pid)).call()
            stage_int = int(stage)
            return int(pid), stage_int
        except ContractLogicError:
            return int(pid), None
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[GOVERNANCE] getProposalStage failed | pid=%s | error=%s",
                pid,
                exc,
            )
            return int(pid), None

    async def _fetch_status(pid: int) -> tuple[int, str | None]:
        pid_res, stage_res = await asyncio.to_thread(
            _get_stage_sync, w3, contract_address, pid
        )
        return pid_res, _classify_proposal_status(stage_res)

    try:
        statuses = await asyncio.gather(*[_fetch_status(pid) for pid in all_ids])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[GOVERNANCE] Failed to resolve proposal statuses | error=%s", exc
        )
        return {
            "Queued": [],
            "Active": [],
        }

    status_by_pid: dict[int, str | None] = {pid: bucket for pid, bucket in statuses}

    # Sort order: `all_ids` is already descending (most recent → oldest).
    result: dict[str, list[int]] = {"Queued": [], "Active": []}

    for pid in all_ids:
        bucket = status_by_pid.get(pid)
        if bucket == "Queued":
            result["Queued"].append(int(pid))
        elif bucket == "Active":
            result["Active"].append(int(pid))

    return result


async def get_historical_proposals_onchain(w3: Web3, contract_address: str) -> list[int]:
    """Return recently concluded governance proposal IDs from on-chain state.

    This is a lightweight best-effort history view built from the IDs present in
    getQueue() and getDequeue(). The Governance contract can keep old IDs in these
    arrays, so we filter by stage and keep only non-active proposals.

    Active stages are {1, 2, 3}. Any other stage value is treated as concluded.

    Args:
        w3: Initialized Web3 instance (HTTPProvider recommended).
        contract_address: Governance contract address (0x...).

    Returns:
        List of up to 40 proposal IDs (desc), excluding active stages.
        Returns [] on failure.
    """
    raw = await asyncio.to_thread(_get_active_proposals_onchain_sync, w3, contract_address)
    queued_raw = raw.get("queued", [])
    dequeued_raw = raw.get("dequeued", [])

    all_ids = sorted(
        {int(x) for x in (queued_raw or []) + (dequeued_raw or []) if int(x) != 0},
        reverse=True,
    )
    if not all_ids:
        return []

    async def _is_concluded(pid: int) -> tuple[int, bool]:
        stage = await asyncio.to_thread(_get_proposal_stage_sync, w3, contract_address, pid)
        is_active = stage in {1, 2, 3}
        return pid, not is_active

    try:
        pairs = await asyncio.gather(*[_is_concluded(pid) for pid in all_ids])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GOVERNANCE] Failed to resolve proposal stages for history | error=%s", exc)
        return []

    concluded_ids = [pid for pid, concluded in pairs if concluded]
    concluded_ids = sorted(set(concluded_ids), reverse=True)
    return concluded_ids[:40]


def _get_proposal_approved_sync(
    w3: Web3, contract_address: str, proposal_id: int
) -> bool | None:
    """Return approved flag from getProposal, or None on revert/error."""
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=GOVERNANCE_ABI_MINIMAL,
        )
        result = contract.functions.getProposal(int(proposal_id)).call()
        return bool(result[6])
    except ContractLogicError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[GOVERNANCE] getProposal failed | proposal_id=%s | error=%s",
            proposal_id,
            exc,
        )
        return None


def resolve_proposal_status_key_sync(
    w3: Web3, contract_address: str, proposal_id: int
) -> str:
    """Map on-chain stage (+ approved) to UX status keys for emoji display.

    Returns one of: ACTIVE, EXPIRED, EXECUTED, REJECTED, UNKNOWN.
    Stage 4 is treated as Expiration (terminal) per local governance UX notes.
    """
    stage = _get_proposal_stage_sync(w3, contract_address, proposal_id)
    if stage is None:
        return "UNKNOWN"
    if stage in {1, 2, 3}:
        return "ACTIVE"
    if stage == 4:
        return "EXPIRED"
    approved = _get_proposal_approved_sync(w3, contract_address, proposal_id)
    if approved is True:
        return "EXECUTED"
    if approved is False:
        return "REJECTED"
    return "UNKNOWN"


async def resolve_proposal_status_key(
    w3: Web3, contract_address: str, proposal_id: int
) -> str:
    """Async wrapper for :func:`resolve_proposal_status_key_sync`."""
    return await asyncio.to_thread(
        resolve_proposal_status_key_sync, w3, contract_address, proposal_id
    )


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

    async def fetch_new_proposals(
        self, last_processed_block: int | None = None
    ) -> tuple[list[dict], int | None]:
        """Fetch newly queued governance proposals from Celo.

        Args:
            last_processed_block: Last block number already processed (stored in DB).
                When None, falls back to current_block - BLOCKS_LOOKBACK.

        Returns:
            Tuple of (proposals, current_block):
              - proposals: list of proposal dicts from ProposalQueued events.
              - current_block: the chain head at fetch time, or None on failure.
            Never raises; returns ([], None) on any failure.
        """
        try:
            w3 = self._w3_primary
            current_block = await self._get_current_block_with_retry(w3)
            if current_block is None or current_block <= 0:
                return [], None

            if last_processed_block is not None:
                start_block = max(0, int(last_processed_block) + 1)
            else:
                start_block = max(0, current_block - BLOCKS_LOOKBACK)

            contract = w3.eth.contract(
                address=GOVERNANCE_ADDRESS, abi=GOVERNANCE_ABI_MINIMAL
            )

            events = await self._get_proposal_events_with_retry(
                contract, start_block, "latest"
            )
            proposals = [self._event_to_dict(e) for e in events]

            logger.info(
                "[GOVERNANCE] Scanned blocks #%s→#%s | found: %s proposals",
                f"{start_block:,}",
                "latest",
                len(proposals),
            )
            return proposals, current_block

        except BlockNotFound as exc:
            logger.warning("[GOVERNANCE] Block not found: %s", exc)
        except ContractLogicError as exc:
            logger.error("[GOVERNANCE] Contract logic error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GOVERNANCE] Unexpected error: %s", exc)

        return [], None

    async def _get_current_block_with_retry(self, w3: Web3) -> int | None:
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
                await asyncio.sleep(delay)
                if attempt >= 1 and self._w3_fallback is not None:
                    w3 = self._w3_fallback
                    logger.warning(
                        "[GOVERNANCE] Switching to fallback RPC for block_number"
                    )
        return None

    async def _get_proposal_events_with_retry(
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
                await asyncio.sleep(delay)
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


