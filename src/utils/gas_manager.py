from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from src.utils.logger import logger


MAX_GAS_PRICE_GWEI = 5.0


@dataclass
class GasPriceTooHighError(Exception):
    """Raised when the current network gas price exceeds the allowed ceiling."""

    gas_price_gwei: float
    max_allowed_gwei: float


class TransactionSimulationError(Exception):
    """Raised when a governance transaction simulation fails."""


def get_safe_gas_price(w3: Web3) -> int:
    """Return a safe gas price in wei or raise when the network is too expensive.

    Args:
        w3: Configured Web3 instance connected to the Celo RPC.

    Returns:
        Gas price in wei as returned by the network when within the safety ceiling.

    Raises:
        GasPriceTooHighError: If the current gas price exceeds MAX_GAS_PRICE_GWEI.
        ValueError: If the provided Web3 instance is not connected.
    """
    if not w3.is_connected():
        raise ValueError("Web3 provider is not connected.")

    gas_price_wei = w3.eth.gas_price
    gas_price_gwei = gas_price_wei / 1e9

    if gas_price_gwei > MAX_GAS_PRICE_GWEI:
        logger.warning(
            "[GAS] Gas price spike detected: %.6f gwei (ceiling: %.2f gwei). "
            "Aborting non-critical transaction.",
            gas_price_gwei,
            MAX_GAS_PRICE_GWEI,
        )
        raise GasPriceTooHighError(
            gas_price_gwei=gas_price_gwei,
            max_allowed_gwei=MAX_GAS_PRICE_GWEI,
        )

    logger.debug(
        "[GAS] Using safe gas price: %.6f gwei (ceiling: %.2f gwei).",
        gas_price_gwei,
        MAX_GAS_PRICE_GWEI,
    )
    return gas_price_wei


def simulate_vote_transaction(
    w3: Web3,
    contract: Contract,
    proposal_id: int,
    vote_value: Any,
    bot_wallet_address: str,
) -> bool:
    """Simulate a governance vote transaction using eth_call before sending it on-chain.

    Args:
        w3: Configured Web3 instance connected to the Celo RPC.
        contract: Web3 contract instance for the governance contract.
        proposal_id: Target proposal identifier.
        vote_value: Raw vote payload, as expected by the contract (e.g. enum or uint256).
        bot_wallet_address: Bot governance wallet address used as tx sender.

    Returns:
        True if the simulation succeeds without reverts.

    Raises:
        TransactionSimulationError: If the simulation fails for any reason.
        ValueError: If the Web3 provider is not connected.
    """
    if not w3.is_connected():
        raise ValueError("Web3 provider is not connected.")

    try:
        tx_params = contract.functions.vote(proposal_id, vote_value).build_transaction(
            {
                "from": bot_wallet_address,
            }
        )
    except Exception as exc:  # pragma: no cover - defensive, contract ABI issues
        logger.error(
            "[GOVERNANCE] Failed to build vote transaction for proposal %s: %s",
            proposal_id,
            exc,
        )
        raise TransactionSimulationError(
            "Failed to build governance vote transaction."
        ) from exc

    try:
        w3.eth.call(tx_params)
        logger.debug(
            "[GOVERNANCE] Simulation succeeded for proposal %s with vote value %s.",
            proposal_id,
            vote_value,
        )
        return True
    except ContractLogicError as exc:
        logger.error(
            "[GOVERNANCE] Simulation failed with contract logic error for proposal %s: %s",
            proposal_id,
            exc,
        )
        raise TransactionSimulationError(
            "Governance vote transaction simulation reverted."
        ) from exc
    except Exception as exc:
        logger.error(
            "[GOVERNANCE] Simulation failed for proposal %s with unexpected error: %s",
            proposal_id,
            exc,
        )
        raise TransactionSimulationError(
            "Governance vote transaction simulation failed due to unexpected error."
        ) from exc

