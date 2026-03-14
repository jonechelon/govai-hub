#!/usr/bin/env python
# scripts/register_agent.py
# Up-to-Celo — standalone ERC-8004 agent registration script
#
# ⚠️  MANUAL STEP — do NOT run this automatically from the bot.
# Run once to register the bot as an on-chain AI agent on Celo Mainnet.
#
# Prerequisites:
#   1. BOT_WALLET_ADDRESS and BOT_WALLET_PRIVATE_KEY set in .env
#   2. erc8004.identity_registry confirmed on celoscan.io (update config.yaml)
#   3. Bot wallet funded with enough CELO for gas (~0.01 CELO)
#
# Usage:
#   python scripts/register_agent.py

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from project root: python scripts/register_agent.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

_CELO_RPC = "https://forno.celo.org"
_CHAIN_ID = 42220
_GAS_LIMIT = 150_000
_OUTPUT_PATH = Path("data/agent_registration.json")

# Minimal ABI for the identity registry register() function.
# The exact signature may vary — confirm on celoscan.io before running.
_REGISTRY_ABI = [
    {
        "inputs": [{"name": "agentUri", "type": "string"}],
        "name": "register",
        "outputs": [{"name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def _load_env() -> tuple[str, str]:
    """Load and validate wallet credentials from environment.

    Returns:
        Tuple of (wallet_address, private_key).

    Raises:
        SystemExit: if any required variable is missing.
    """
    wallet = os.getenv("BOT_WALLET_ADDRESS", "").strip()
    private_key = os.getenv("BOT_WALLET_PRIVATE_KEY", "").strip()

    if not wallet:
        print("❌ BOT_WALLET_ADDRESS is not set in .env")
        sys.exit(1)
    if not private_key:
        print("❌ BOT_WALLET_PRIVATE_KEY is not set in .env")
        sys.exit(1)

    return wallet, private_key


def _load_erc8004_config() -> dict:
    """Load ERC-8004 config from config.yaml.

    Returns:
        The erc8004 config dict.

    Raises:
        SystemExit: if config is missing or identity_registry is still TODO.
    """
    try:
        import yaml
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        with config_path.open("r") as fh:
            cfg = yaml.safe_load(fh)
    except Exception as exc:
        print(f"❌ Could not load config.yaml: {exc}")
        sys.exit(1)

    erc8004 = cfg.get("erc8004", {})
    if not erc8004:
        print("❌ config.yaml missing [erc8004] section")
        sys.exit(1)

    return erc8004


def _build_agent_uri(erc8004: dict) -> str:
    """Build a data URI containing the base64-encoded ERC-8004 agent JSON.

    Args:
        erc8004: the erc8004 section from config.yaml.

    Returns:
        data URI string suitable for passing to the registry contract.
    """
    description = erc8004.get("agent_description", "")
    if len(description) > 200:
        description = description[:197] + "..."

    agent_json = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": erc8004.get("agent_name", "Up-to-Celo"),
        "description": description,
        "services": [
            {
                "name": "telegram",
                "endpoint": erc8004.get("agent_endpoint", "https://t.me/uptocelo_bot"),
            }
        ],
        "x402Support": False,
        "active": True,
    }

    encoded = base64.b64encode(json.dumps(agent_json, separators=(",", ":")).encode()).decode()
    return f"data:application/json;base64,{encoded}"


def _confirm_proceed(registry_address: str) -> bool:
    """Display a warning and prompt for user confirmation.

    Args:
        registry_address: the identity_registry address from config.yaml.

    Returns:
        True if the user confirmed, False otherwise.
    """
    print()
    print("=" * 60)
    print("⚠️   ERC-8004 Agent Registration")
    print("=" * 60)
    print()
    print(f"  identity_registry: {registry_address}")
    print()
    if "TODO" in registry_address:
        print("  ⚠️  identity_registry in config.yaml is still TODO.")
        print("      Confirm the correct address on celoscan.io first.")
        print("      Update config.yaml → erc8004.identity_registry before proceeding.")
        print()
        print("  Aborting.")
        return False

    print("  ⚠️  This will submit an on-chain transaction on Celo Mainnet.")
    print("      Make sure your bot wallet has enough CELO for gas (~0.01 CELO).")
    print()
    answer = input("  Proceed? [y/N]: ").strip().lower()
    return answer == "y"


def main() -> None:
    """Run the ERC-8004 agent registration flow."""
    wallet_address, private_key = _load_env()
    erc8004 = _load_erc8004_config()
    registry_address = erc8004.get("identity_registry", "0xTODO")

    if not _confirm_proceed(registry_address):
        print("\nAborted — no transaction sent.\n")
        sys.exit(0)

    # Connect to Celo Mainnet
    w3 = Web3(Web3.HTTPProvider(_CELO_RPC, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"❌ Could not connect to Celo RPC: {_CELO_RPC}")
        sys.exit(1)

    chain_id = w3.eth.chain_id
    if chain_id != _CHAIN_ID:
        print(f"❌ Wrong chain: expected {_CHAIN_ID} (Celo Mainnet), got {chain_id}")
        sys.exit(1)

    checksum_registry = Web3.to_checksum_address(registry_address)
    checksum_wallet = Web3.to_checksum_address(wallet_address)

    contract = w3.eth.contract(address=checksum_registry, abi=_REGISTRY_ABI)
    agent_uri = _build_agent_uri(erc8004)

    print()
    print(f"  Wallet:   {checksum_wallet}")
    print(f"  Registry: {checksum_registry}")
    print(f"  Chain ID: {chain_id}")
    print()

    # Estimate gas
    try:
        estimated_gas = contract.functions.register(agent_uri).estimate_gas(
            {"from": checksum_wallet}
        )
        gas_limit = min(int(estimated_gas * 1.2), _GAS_LIMIT * 2)
        print(f"  Estimated gas: {estimated_gas:,} | Using: {gas_limit:,}")
    except Exception as exc:
        print(f"  ⚠️  Gas estimation failed: {exc} — using default {_GAS_LIMIT:,}")
        gas_limit = _GAS_LIMIT

    # Build and sign transaction
    nonce = w3.eth.get_transaction_count(checksum_wallet)
    gas_price = w3.eth.gas_price

    tx = contract.functions.register(agent_uri).build_transaction(
        {
            "from": checksum_wallet,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "chainId": _CHAIN_ID,
        }
    )

    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)

    print("\n  Submitting transaction...")
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_hash_hex = tx_hash.hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex
    print(f"  TX hash: {tx_hash_hex}")
    print("  Waiting for confirmation...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] != 1:
        print(f"❌ Transaction reverted! hash: {tx_hash_hex}")
        sys.exit(1)

    # Decode agentId from receipt logs
    agent_id: int | str = "unknown"
    try:
        logs = contract.events.AgentRegistered().process_receipt(receipt)  # type: ignore[attr-defined]
        if logs:
            agent_id = logs[0]["args"].get("agentId", "unknown")
    except Exception:
        # AgentRegistered event may have a different name — parse raw logs as fallback
        for log in receipt.get("logs", []):
            if log.get("topics") and len(log["data"]) >= 66:
                try:
                    agent_id = int(log["data"].hex()[-64:], 16)
                    break
                except Exception:
                    pass

    # Persist registration record
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "agent_id": agent_id,
        "tx_hash": tx_hash_hex,
        "wallet": checksum_wallet,
        "registry": checksum_registry,
        "registered_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _OUTPUT_PATH.write_text(json.dumps(record, indent=2))

    print()
    print(f"✅ Agent registered! agentId: {agent_id}")
    print(f"   TX: {tx_hash_hex}")
    print(f"   Receipt saved to: {_OUTPUT_PATH}")
    print()
    print(
        f"Tweet: Submitting @uptocelo_bot to #CeloHackathon — "
        f"agentId: {agent_id} @Celo @CeloDevs"
    )
    print()

    print(
        "[ERC8004] Agent registered | agentId: %s | tx: %s | registry: eip155:%d:%s"
        % (agent_id, tx_hash_hex, _CHAIN_ID, checksum_registry)
    )


if __name__ == "__main__":
    main()
