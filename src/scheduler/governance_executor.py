from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from typing import Any

from telegram import Bot
from telegram.error import Forbidden
from web3 import Web3
from web3.contract import Contract

from src.database.manager import db
from src.fetchers.governance_fetcher import GOVERNANCE_ABI_MINIMAL, GOVERNANCE_ADDRESS
from src.utils.defi_links import build_venue_links
from src.utils.env_validator import get_env_or_fail
from src.utils.gas_manager import (
    TransactionSimulationError,
    simulate_vote_transaction,
)

logger = logging.getLogger(__name__)

GAS_PRICE_MULTIPLIER = float(os.getenv("GAS_PRICE_MULTIPLIER", "2.0"))


async def get_dynamic_gas_params(w3: Web3) -> dict[str, int]:
    """Return EIP-1559 gas params based on current network conditions."""
    loop = asyncio.get_event_loop()
    try:
        latest = await loop.run_in_executor(None, w3.eth.get_block, "latest")
        base_fee = latest.get("baseFeePerGas")
        if base_fee:
            max_priority = await loop.run_in_executor(None, lambda: w3.eth.max_priority_fee)
            max_fee = int(base_fee * GAS_PRICE_MULTIPLIER) + int(max_priority)
            logger.info(
                "[GAS] Dynamic fee | baseFee=%sgwei maxFee=%sgwei",
                int(base_fee) // 10**9,
                int(max_fee) // 10**9,
            )
            return {
                "maxFeePerGas": int(max_fee),
                "maxPriorityFeePerGas": int(max_priority),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GAS] EIP-1559 fetch failed, falling back: %s", exc)

    gas_price = await loop.run_in_executor(None, lambda: w3.eth.gas_price)
    return {"gasPrice": int(gas_price * GAS_PRICE_MULTIPLIER)}


GOVERNANCE_VOTE_ABI: list[dict[str, Any]] = [
    {
        "constant": False,
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "index", "type": "uint256"},
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


async def _notify_auto_trades(bot: Bot, proposal_id: int, proposal_title: str) -> None:
    """
    Sends DeFi action links to all users with pending auto_trades for a given proposal.
    Called when a governance proposal status changes to Executed.
    Marks each trade as notified=1 after sending — never re-notifies.
    """
    try:
        rows = await db.get_pending_auto_trades(proposal_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[AUTO_TRADE] DB fetch failed for proposal %s: %s", proposal_id, exc)
        return

    for row in rows:
        trade_id = row["id"]
        user_id = row["user_id"]
        try:
            intent = json.loads(row["intent_json"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("[AUTO_TRADE] Bad intent_json for trade %s", trade_id)
            continue

        keyboard = build_venue_links(intent)

        message = (
            "🔔 <b>Auto-Trade Alert</b>\n\n"
            f"Governance proposal <b>{proposal_title}</b> has been executed.\n\n"
            "Your pending trade intent is ready. Open a venue to complete it "
            "in your wallet:\n"
            "<i>Sign in your wallet — this bot never holds your keys.</i>"
        )

        try:
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            await db.mark_auto_trade_notified(trade_id)
            logger.info("[AUTO_TRADE] Notified user %s for trade %s", user_id, trade_id)

        except Forbidden:
            logger.warning(
                "[AUTO_TRADE] Forbidden for user %s — marking notified", user_id
            )
            await db.mark_auto_trade_notified(trade_id)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[AUTO_TRADE] Failed to notify user %s trade %s: %s",
                user_id,
                trade_id,
                exc,
            )


class GovernanceExecutor:
    """Periodic job that executes aggregated governance votes on-chain.

    This job:
        1. Aggregates pending vote intents from the database by proposal_id.
        2. Computes the majority choice (YES/NO/ABSTAIN) per proposal.
        3. Verifies that the configured GOVERNANCE_PRIVATE_KEY matches GOVERNANCE_DELEGATE_ADDRESS.
        4. Applies gas price safety checks and transaction simulation.
        5. Signs and sends a single governance vote transaction per proposal
           using the dedicated GOVERNANCE wallet (separate from the treasury BOT_WALLET).
    """

    def __init__(self) -> None:
        rpc_url = get_env_or_fail("CELO_RPC_URL")
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

        # Governance wallet — dedicated to signing on-chain vote transactions.
        # Kept strictly separate from BOT_WALLET (treasury) for security isolation.
        self._governance_private_key = get_env_or_fail("GOVERNANCE_PRIVATE_KEY")
        self._governance_delegate_address = Web3.to_checksum_address(
            get_env_or_fail("GOVERNANCE_DELEGATE_ADDRESS")
        )

        self._contract: Contract = self._w3.eth.contract(
            address=GOVERNANCE_ADDRESS,
            abi=GOVERNANCE_VOTE_ABI,
        )
        self._governance_read: Contract = self._w3.eth.contract(
            address=GOVERNANCE_ADDRESS,
            abi=GOVERNANCE_ABI_MINIMAL,
        )

    async def _run_sync(self, func, *args):
        """Run a blocking web3 call in the default executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    def _proposal_passed_and_concluded_sync(self, proposal_id: int) -> bool:
        """Return True when voting ended and the proposal was approved (executed)."""
        try:
            stage = self._governance_read.functions.getProposalStage(
                int(proposal_id)
            ).call()
            stage_int = int(stage)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[AUTO_TRADE] getProposalStage failed for proposal %s: %s",
                proposal_id,
                exc,
            )
            return False
        if stage_int in {1, 2, 3}:
            return False
        try:
            proposal = self._governance_read.functions.getProposal(
                int(proposal_id)
            ).call()
            approved = bool(proposal[6])
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[AUTO_TRADE] getProposal failed for proposal %s: %s",
                proposal_id,
                exc,
            )
            return False
        return approved

    async def _notify_executed_auto_trades(self, bot: Bot) -> None:
        """Notify users when on-chain state shows a proposal passed and concluded."""
        proposal_ids = await db.get_proposal_ids_with_pending_auto_trades()
        for proposal_id in proposal_ids:
            passed = await self._run_sync(
                self._proposal_passed_and_concluded_sync,
                proposal_id,
            )
            if not passed:
                continue
            title = html.escape(f"Proposal #{proposal_id}")
            await _notify_auto_trades(bot, proposal_id, title)

    async def run(self, bot: Bot | None = None) -> None:
        """Entry point scheduled by APScheduler."""
        try:
            if bot is not None:
                await self._notify_executed_auto_trades(bot)
            await self._execute_pending_votes()
        except Exception as exc:  # noqa: BLE001
            logger.error("[GOVERNANCE] Executor error: %s", exc, exc_info=True)

    def _verify_governance_wallet(self) -> bool:
        """Verify that GOVERNANCE_PRIVATE_KEY derives the expected GOVERNANCE_DELEGATE_ADDRESS.

        Returns:
            True if the derived address matches the configured delegate address.

        Side-effects:
            Logs an error and returns False on any mismatch or derivation failure.
        """
        try:
            derived_address = self._w3.eth.account.from_key(
                self._governance_private_key
            ).address
            derived_checksum = Web3.to_checksum_address(derived_address)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[GOVERNANCE] Failed to derive address from GOVERNANCE_PRIVATE_KEY: %s", exc
            )
            return False

        if derived_checksum != self._governance_delegate_address:
            logger.error(
                "[GOVERNANCE] Governance wallet mismatch — aborting execution cycle. "
                "GOVERNANCE_PRIVATE_KEY derives %s but GOVERNANCE_DELEGATE_ADDRESS is %s. "
                "Check your .env configuration.",
                derived_checksum,
                self._governance_delegate_address,
            )
            return False

        return True

    async def _execute_pending_votes(self) -> None:
        """Fetch aggregated pending votes and execute majority decisions on-chain."""
        # Security guard: abort the cycle if the governance wallet is misconfigured.
        if not self._verify_governance_wallet():
            return

        aggregated = await db.get_pending_votes_aggregated()
        if not aggregated:
            logger.info("[GOVERNANCE] No pending governance votes to execute")
            return

        logger.info(
            "[GOVERNANCE] Executor started | pending_proposals=%s | signer=%s",
            len(aggregated),
            self._governance_delegate_address,
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

            # Safety step 1 — transaction simulation (dry-run)
            try:
                simulate_vote_transaction(
                    w3=self._w3,
                    contract=self._contract,
                    proposal_id=proposal_id,
                    vote_value=vote_value,
                    bot_wallet_address=self._governance_delegate_address,
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
    ) -> None:
        """Build, sign, and send the governance vote transaction.

        Uses the dedicated governance wallet (GOVERNANCE_PRIVATE_KEY / GOVERNANCE_DELEGATE_ADDRESS),
        keeping it strictly isolated from the treasury BOT_WALLET.
        """
        nonce = await self._run_sync(
            self._w3.eth.get_transaction_count,
            self._governance_delegate_address,
        )
        chain_id = await self._run_sync(lambda: self._w3.eth.chain_id)
        gas_params = await get_dynamic_gas_params(self._w3)

        tx_dict = self._contract.functions.vote(
            proposal_id,
            0,
            vote_value,
        ).build_transaction(
            {
                "from": self._governance_delegate_address,
                "nonce": nonce,
                "chainId": chain_id,
                **gas_params,
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
            private_key=self._governance_private_key,
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

