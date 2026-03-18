from __future__ import annotations

import asyncio
import logging
from typing import Any

from web3 import Web3
from web3.contract import Contract

from src.database.manager import db
from src.fetchers.governance_fetcher import GOVERNANCE_ADDRESS
from src.utils.env_validator import get_env_or_fail
from src.utils.gas_manager import (
    GasPriceTooHighError,
    TransactionSimulationError,
    get_safe_gas_price,
    simulate_vote_transaction,
)

logger = logging.getLogger(__name__)


GOVERNANCE_VOTE_ABI: list[dict[str, Any]] = [
    {
        "constant": False,
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "vote",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Mapping from majority choice string to on-chain enum numeric value.
# Based on Celo Governance Proposals.VoteValue enum:
#   None=0, Abstain=1, No=2, Yes=3
VOTE_VALUE_MAP: dict[str, int] = {
    "ABSTAIN": 1,
    "NO": 2,
    "YES": 3,
}


class GovernanceExecutor:
    """Periodic job that executes aggregated governance votes on-chain.

    This job:
        1. Aggregates pending vote intents from the database by proposal_id.
        2. Computes the majority choice (YES/NO/ABSTAIN) per proposal.
        3. Applies gas price safety checks and transaction simulation.
        4. Signs and sends a single governance vote transaction per proposal
           using the BOTWALLET keys.
    """

    def __init__(self) -> None:
        rpc_url = get_env_or_fail("CELO_RPC_URL")
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
        self._bot_wallet_address = Web3.to_checksum_address(
            get_env_or_fail("BOT_WALLET_ADDRESS")
        )
        self._bot_wallet_private_key = get_env_or_fail("BOT_WALLET_PRIVATE_KEY")
        self._contract: Contract = self._w3.eth.contract(
            address=GOVERNANCE_ADDRESS,
            abi=GOVERNANCE_VOTE_ABI,
        )

    async def _run_sync(self, func, *args):
        """Run a blocking web3 call in the default executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    async def run(self) -> None:
        """Entry point scheduled by APScheduler."""
        try:
            await self._execute_pending_votes()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GOVERNANCE] Executor job failed: %s", exc)

    async def _execute_pending_votes(self) -> None:
        """Fetch aggregated pending votes and execute majority decisions on-chain."""
        aggregated = await db.get_pending_votes_aggregated()
        if not aggregated:
            logger.info("[GOVERNANCE] No pending governance votes to execute")
            return

        logger.info(
            "[GOVERNANCE] Executor started | pending_proposals=%s",
            len(aggregated),
        )

        for item in aggregated:
            proposal_id: int = int(item["proposal_id"])
            majority_choice: str = str(item["majority_choice"]).upper()
            user_ids: list[int] = list(item.get("user_ids", []))

            vote_value = VOTE_VALUE_MAP.get(majority_choice)
            if vote_value is None:
                logger.warning(
                    "[GOVERNANCE] Skipping proposal %s due to unsupported majority choice: %s",
                    proposal_id,
                    majority_choice,
                )
                continue

            logger.info(
                "[GOVERNANCE] Executing majority vote | proposal_id=%s | choice=%s | voters=%s",
                proposal_id,
                majority_choice,
                len(user_ids),
            )

            # Safety step 1 — gas price ceiling
            try:
                gas_price_wei = get_safe_gas_price(self._w3)
            except GasPriceTooHighError as exc:
                logger.info(
                    "[GOVERNANCE] Gas price too high — aborting executor job "
                    "| current=%.6f gwei | ceiling=%.2f gwei",
                    exc.gas_price_gwei,
                    exc.max_allowed_gwei,
                )
                # Abort entire job to retry on next cycle.
                return

            # Safety step 2 — transaction simulation (dry-run)
            try:
                simulate_vote_transaction(
                    w3=self._w3,
                    contract=self._contract,
                    proposal_id=proposal_id,
                    vote_value=vote_value,
                    bot_wallet_address=self._bot_wallet_address,
                )
            except TransactionSimulationError as exc:
                logger.error(
                    "[GOVERNANCE] Simulation failed for proposal %s — skipping execution: %s",
                    proposal_id,
                    exc,
                )
                # Future improvement: mark proposal as failed in a dedicated column.
                continue

            try:
                await self._send_vote_transaction(
                    proposal_id=proposal_id,
                    vote_value=vote_value,
                    gas_price_wei=gas_price_wei,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[GOVERNANCE] Failed to send vote transaction for proposal %s: %s",
                    proposal_id,
                    exc,
                )
                continue

    async def _send_vote_transaction(
        self,
        proposal_id: int,
        vote_value: int,
        gas_price_wei: int,
    ) -> None:
        """Build, sign, and send the governance vote transaction."""
        nonce = await self._run_sync(
            self._w3.eth.get_transaction_count,
            self._bot_wallet_address,
        )

        tx_dict = self._contract.functions.vote(
            proposal_id,
            vote_value,
        ).build_transaction(
            {
                "from": self._bot_wallet_address,
                "nonce": nonce,
                "gasPrice": gas_price_wei,
            }
        )

        # Estimate gas to avoid obvious underestimation; fall back to existing gas on failure.
        try:
            gas_estimate = await self._run_sync(self._w3.eth.estimate_gas, tx_dict)
            tx_dict["gas"] = int(gas_estimate * 1.2)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[GOVERNANCE] Gas estimation failed for proposal %s: %s — using existing gas field",
                proposal_id,
                exc,
            )

        signed = self._w3.eth.account.sign_transaction(
            tx_dict,
            private_key=self._bot_wallet_private_key,
        )

        tx_hash_bytes = await self._run_sync(
            self._w3.eth.send_raw_transaction,
            signed.rawTransaction,
        )
        tx_hash = self._w3.to_hex(tx_hash_bytes)

        logger.info(
            "[GOVERNANCE] Vote transaction sent | proposal_id=%s | tx=%s",
            proposal_id,
            tx_hash,
        )

        await db.mark_votes_executed(proposal_id=proposal_id, tx_hash=tx_hash)


# Module-level singleton used by scheduler
governance_executor = GovernanceExecutor()

