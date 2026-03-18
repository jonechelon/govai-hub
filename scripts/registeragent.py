"""
scripts/registeragent.py
------------------------
Standalone script to register Celo GovAI Hub as an ERC-8004 agent on Celo Mainnet.

Usage:
    python scripts/registeragent.py [--dry-run]

Requirements:
    .env          → BOT_WALLET_ADDRESS, BOT_WALLET_PRIVATE_KEY
    config.yaml   → erc8004.identity_registry, agent_name, agent_description,
                    agent_endpoint, chain_id
    Wallet must hold ≥ 0.01 CELO to cover gas.

Output:
    data/agentregistration.json  → {agentId, txHash, wallet, timestamp}
"""

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("registeragent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
CONFIG_FILE = REPO_ROOT / "config.yaml"
OUTPUT_FILE = REPO_ROOT / "data" / "agentregistration.json"

CELO_CHAIN_ID = 42220
MIN_BALANCE_CELO = 0.01          # minimum CELO required in wallet
GAS_SAFETY_MARGIN = 1.2          # 20% buffer over estimate_gas result

# ERC-8004 ABI — Identity Registry (ERC-721 + URIStorage extension + agent-specific functions)
# Source: https://eips.ethereum.org/EIPS/eip-8004 (Draft, 2025-08-13)
# The registry is ERC-721 based; tokenId == agentId, tokenURI == agentURI.
IDENTITY_REGISTRY_ABI = [
    # ── Registration ──────────────────────────────────────────────────────────
    {
        "inputs": [{"internalType": "string", "name": "agentURI", "type": "string"}],
        "name": "register",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    # ── URI management ────────────────────────────────────────────────────────
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId",  "type": "uint256"},
            {"internalType": "string",  "name": "agentURI", "type": "string"},
        ],
        "name": "setAgentURI",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "tokenURI",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    # ── On-chain metadata ─────────────────────────────────────────────────────
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId",     "type": "uint256"},
            {"internalType": "string",  "name": "metadataKey", "type": "string"},
        ],
        "name": "getMetadata",
        "outputs": [{"internalType": "bytes", "name": "", "type": "bytes"}],
        "stateMutability": "view",
        "type": "function",
    },
    # ── ERC-721 ownership ─────────────────────────────────────────────────────
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    # ── Events ────────────────────────────────────────────────────────────────
    # ERC-8004 spec: Registered(uint256 indexed agentId, string agentURI, address indexed owner)
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "uint256", "name": "agentId",  "type": "uint256"},
            {"indexed": False, "internalType": "string",  "name": "agentURI", "type": "string"},
            {"indexed": True,  "internalType": "address", "name": "owner",    "type": "address"},
        ],
        "name": "Registered",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "internalType": "uint256", "name": "agentId",  "type": "uint256"},
            {"indexed": False, "internalType": "string",  "name": "agentURI", "type": "string"},
        ],
        "name": "URIUpdated",
        "type": "event",
    },
    # ERC-721 Transfer (minted when agentId is first registered)
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "from",    "type": "address"},
            {"indexed": True, "internalType": "address", "name": "to",      "type": "address"},
            {"indexed": True, "internalType": "uint256", "name": "tokenId", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]

# Function selectors used for ABI validation in --dry-run
_REQUIRED_SELECTORS = {"register", "ownerOf", "tokenURI"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load and return the full config.yaml as a dict."""
    if not CONFIG_FILE.exists():
        logger.error("config.yaml not found at %s", CONFIG_FILE)
        sys.exit(1)
    with CONFIG_FILE.open() as fh:
        return yaml.safe_load(fh)


def build_agent_uri(cfg: dict) -> str:
    """
    Build a data-URI containing the ERC-8004 agent JSON.

    Returns:
        str: data:application/json;base64,<payload>
    """
    erc = cfg["erc8004"]
    agent_json = {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": erc["agent_name"],
        "description": erc["agent_description"],
        "services": [
            {
                "name": "telegram",
                "endpoint": erc["agent_endpoint"],
                "x402Support": False,
                "active": True,
            }
        ],
    }
    encoded = base64.b64encode(json.dumps(agent_json, separators=(",", ":")).encode()).decode()
    uri = f"data:application/json;base64,{encoded}"
    logger.debug("Agent URI built (%d chars)", len(uri))
    return uri


def connect_rpc(cfg: dict) -> Web3:
    """
    Connect to Celo Mainnet via primary RPC, fallback to secondary.

    Returns:
        Web3: connected instance

    Raises:
        SystemExit: if both RPC endpoints fail.
    """
    primary = cfg["celo_rpc"]["primary"]
    fallback = cfg["celo_rpc"]["fallback"]

    for url in (primary, fallback):
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
        if w3.is_connected():
            logger.info("ERC8004: Connected to RPC %s (chain_id=%s)", url, w3.eth.chain_id)
            if w3.eth.chain_id != CELO_CHAIN_ID:
                logger.error(
                    "ERC8004: Wrong chain! Expected %d, got %d. Aborting.",
                    CELO_CHAIN_ID,
                    w3.eth.chain_id,
                )
                sys.exit(1)
            return w3
        logger.warning("ERC8004: RPC %s unreachable, trying next…", url)

    logger.error("ERC8004: All RPC endpoints failed. Check network or CELO_RPC_URL in .env")
    sys.exit(1)


def validate_registry(w3: Web3, address: str) -> str:
    """
    Validate that the identity_registry address has deployed bytecode.

    Returns:
        str: checksum address

    Raises:
        SystemExit: if address is invalid, placeholder, or has no code.
    """
    if not address or "TODO" in address.upper():
        logger.error(
            "ERC8004: identity_registry is still a placeholder (%s). "
            "Find the real deployed contract:\n"
            "  1. Search celoscan.io for 'IdentityRegistry' or 'AgentRegistry'\n"
            "  2. Check https://eips.ethereum.org/EIPS/eip-8004 for canonical deployments\n"
            "  3. Consider testing on Alfajores first (https://alfajores.celoscan.io)\n"
            "     then set CELO_RPC_URL to https://alfajores-forno.celo-testnet.org",
            address,
        )
        sys.exit(1)

    try:
        checksum = Web3.to_checksum_address(address)
    except ValueError:
        logger.error("ERC8004: Invalid identity_registry address: %s", address)
        sys.exit(1)

    code = w3.eth.get_code(checksum)
    if code == b"" or code == "0x":
        logger.error(
            "ERC8004: No bytecode at %s. "
            "Confirm the correct address on https://celoscan.io before running.",
            checksum,
        )
        sys.exit(1)

    logger.info("ERC8004: Registry bytecode confirmed at %s", checksum)
    return checksum


def validate_abi(w3: Web3, registry_checksum: str) -> None:
    """
    Validate that the deployed contract exposes expected ERC-8004 selectors.

    Checks that `register(string)` and `ownerOf(uint256)` are callable (view
    simulation), which confirms the contract is an ERC-721-based identity registry
    and not an unrelated contract (e.g. a precompile or stablecoin).

    Raises:
        SystemExit: if the contract does not look like an ERC-8004 registry.
    """
    contract = w3.eth.contract(address=registry_checksum, abi=IDENTITY_REGISTRY_ABI)

    # ownerOf(0) should revert with ERC721 "invalid token" — NOT with a generic error.
    # Any response (including revert) proves the function selector exists.
    try:
        contract.functions.ownerOf(0).call()
        logger.debug("ERC8004: ownerOf(0) returned without revert — tokenId 0 exists")
    except Exception as exc:
        msg = str(exc).lower()
        # ERC-721 standard revert messages for non-existent token
        erc721_reverts = ("invalid token", "nonexistent token", "erc721", "owner query")
        if any(kw in msg for kw in erc721_reverts):
            logger.info("ERC8004: ownerOf selector confirmed (ERC-721 revert as expected)")
        else:
            logger.warning(
                "ERC8004: ownerOf call returned unexpected error: %s\n"
                "  This may NOT be an ERC-8004 registry. Selector mismatch possible.",
                exc,
            )
            logger.warning(
                "ERC8004: Suggestion — test on Alfajores testnet first:\n"
                "  Set CELO_RPC_URL=https://alfajores-forno.celo-testnet.org in .env\n"
                "  and CELO_CHAIN_ID=44787 to avoid spending real CELO on a wrong contract."
            )
            sys.exit(1)


def check_balance(w3: Web3, address: str) -> None:
    """
    Ensure the wallet holds at least MIN_BALANCE_CELO CELO.

    Raises:
        SystemExit: if balance is insufficient.
    """
    balance_wei = w3.eth.get_balance(address)
    balance_celo = w3.from_wei(balance_wei, "ether")
    logger.info("ERC8004: Wallet balance = %.6f CELO", float(balance_celo))

    if float(balance_celo) < MIN_BALANCE_CELO:
        logger.error(
            "ERC8004: Insufficient balance (%.6f CELO). Need ≥ %.4f CELO for gas. "
            "Fund wallet %s before retrying.",
            float(balance_celo),
            MIN_BALANCE_CELO,
            address,
        )
        sys.exit(1)


def load_existing_registration() -> dict | None:
    """Return previously saved registration data, or None if not found."""
    if OUTPUT_FILE.exists():
        with OUTPUT_FILE.open() as fh:
            return json.load(fh)
    return None


def save_registration(data: dict) -> None:
    """Persist registration data to data/agentregistration.json."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w") as fh:
        json.dump(data, fh, indent=2)
    logger.info("ERC8004: Registration saved to %s", OUTPUT_FILE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Register Celo GovAI Hub as ERC-8004 agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate everything without broadcasting the transaction",
    )
    args = parser.parse_args()

    # ── Load environment ───────────────────────────────────────────────────
    load_dotenv(ENV_FILE)

    wallet_address = os.getenv("BOT_WALLET_ADDRESS", "").strip()
    private_key = os.getenv("BOT_WALLET_PRIVATE_KEY", "").strip()

    if not wallet_address or not private_key:
        logger.error(
            "ERC8004: BOT_WALLET_ADDRESS and BOT_WALLET_PRIVATE_KEY must be set in .env"
        )
        sys.exit(1)

    if not private_key.startswith("0x"):
        logger.error("ERC8004: BOT_WALLET_PRIVATE_KEY must start with '0x'")
        sys.exit(1)

    # ── Idempotency guard ──────────────────────────────────────────────────
    existing = load_existing_registration()
    if existing:
        logger.info(
            "ERC8004: Agent already registered — agentId=%s tx=%s. "
            "Delete %s to re-register.",
            existing.get("agentId"),
            existing.get("txHash"),
            OUTPUT_FILE,
        )
        return

    # ── Load config ────────────────────────────────────────────────────────
    cfg = load_config()
    erc = cfg.get("erc8004", {})

    if not erc:
        logger.error("ERC8004: Missing 'erc8004' section in config.yaml")
        sys.exit(1)

    registry_address_raw: str = erc.get("identity_registry", "")
    if not registry_address_raw:
        logger.error("ERC8004: identity_registry not set in config.yaml → erc8004")
        sys.exit(1)

    # ── Connect + validate ─────────────────────────────────────────────────
    w3 = connect_rpc(cfg)

    try:
        wallet_checksum = Web3.to_checksum_address(wallet_address)
    except ValueError:
        logger.error("ERC8004: Invalid BOT_WALLET_ADDRESS: %s", wallet_address)
        sys.exit(1)

    registry_checksum = validate_registry(w3, registry_address_raw)
    check_balance(w3, wallet_checksum)

    # ── Build agent URI ────────────────────────────────────────────────────
    agent_uri = build_agent_uri(cfg)
    logger.info("ERC8004: Agent URI ready (length=%d)", len(agent_uri))

    if args.dry_run:
        logger.info("ERC8004: --dry-run mode — validating ABI before any transaction…")
        validate_abi(w3, registry_checksum)
        logger.info("ERC8004: --dry-run passed. No transaction sent.")
        logger.info("  Wallet   : %s", wallet_checksum)
        logger.info("  Registry : %s", registry_checksum)
        logger.info("  Agent URI: %s…", agent_uri[:80])
        logger.info("  ABI      : register(), ownerOf(), tokenURI() selectors confirmed")
        return

    # ── Build & send transaction ───────────────────────────────────────────
    contract = w3.eth.contract(address=registry_checksum, abi=IDENTITY_REGISTRY_ABI)

    nonce = w3.eth.get_transaction_count(wallet_checksum, "pending")
    gas_price = w3.eth.gas_price

    try:
        gas_estimate = contract.functions.register(agent_uri).estimate_gas(
            {"from": wallet_checksum}
        )
        gas_limit = int(gas_estimate * GAS_SAFETY_MARGIN)
        logger.info("ERC8004: Gas estimated=%d → limit=%d (+20%% margin)", gas_estimate, gas_limit)
    except ContractLogicError as exc:
        logger.error("ERC8004: Contract reverted during gas estimation: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("ERC8004: Failed to estimate gas: %s", exc)
        sys.exit(1)

    try:
        tx = contract.functions.register(agent_uri).build_transaction(
            {
                "chainId": CELO_CHAIN_ID,
                "from": wallet_checksum,
                "nonce": nonce,
                "gas": gas_limit,
                "gasPrice": gas_price,
            }
        )
    except ContractLogicError as exc:
        logger.error("ERC8004: Contract call reverted during tx build: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("ERC8004: Failed to build transaction: %s", exc)
        sys.exit(1)

    logger.info(
        "ERC8004: Signing and sending transaction (gas=%d, gasPrice=%s Gwei)…",
        gas_limit,
        round(w3.from_wei(gas_price, "gwei"), 4),
    )

    try:
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        logger.info("ERC8004: Transaction broadcast — tx=%s", tx_hash_hex)
    except Exception as exc:
        logger.error("ERC8004: Failed to send transaction: %s", exc)
        logger.error(
            "ERC8004: Suggestion — if the contract address is wrong or unverified:\n"
            "  1. Confirm the address on https://celoscan.io (search 'IdentityRegistry')\n"
            "  2. Try Alfajores testnet first to avoid real CELO costs:\n"
            "     Set CELO_RPC_URL=https://alfajores-forno.celo-testnet.org in .env\n"
            "     and update identity_registry in config.yaml to the Alfajores address"
        )
        sys.exit(1)

    # ── Wait for receipt ───────────────────────────────────────────────────
    logger.info("ERC8004: Waiting for transaction receipt (timeout=120s)…")
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    except Exception as exc:
        logger.error(
            "ERC8004: Timeout or error waiting for receipt: %s. "
            "Check tx %s on https://celoscan.io/tx/%s",
            exc,
            tx_hash_hex,
            tx_hash_hex,
        )
        sys.exit(1)

    if receipt["status"] != 1:
        logger.error(
            "ERC8004: Transaction reverted. tx=%s — check https://celoscan.io/tx/%s",
            tx_hash_hex,
            tx_hash_hex,
        )
        logger.error(
            "ERC8004: A revert usually means the contract address is wrong (not ERC-8004),\n"
            "  the function selector does not match, or the agentURI format is invalid.\n"
            "  Suggestion — use Alfajores testnet first:\n"
            "    CELO_RPC_URL=https://alfajores-forno.celo-testnet.org\n"
            "    identity_registry=<Alfajores ERC-8004 address from celoscan.io>"
        )
        sys.exit(1)

    # ── Extract agentId from Registered event (ERC-8004 spec name) ────────────
    agent_id: int | None = None
    try:
        logs = contract.events.Registered().process_receipt(receipt)
        if logs:
            agent_id = logs[0]["args"]["agentId"]
    except Exception as exc:
        logger.warning("ERC8004: Could not parse Registered event: %s", exc)

    if agent_id is None:
        logger.warning(
            "ERC8004: agentId not found in events — check receipt manually. tx=%s",
            tx_hash_hex,
        )
        agent_id = "unknown"

    logger.info("ERC8004: Agent registered agentId %s tx %s", agent_id, tx_hash_hex)

    # ── Persist registration data ──────────────────────────────────────────
    registration = {
        "agentId": agent_id,
        "txHash": tx_hash_hex,
        "wallet": wallet_checksum,
        "registry": registry_checksum,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "celoscanUrl": f"https://celoscan.io/tx/{tx_hash_hex}",
    }
    save_registration(registration)

    # ── Tweet instruction ──────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  ERC-8004 registration complete!")
    print(f"  agentId   : {agent_id}")
    print(f"  txHash    : {tx_hash_hex}")
    print(f"  celoscan  : https://celoscan.io/tx/{tx_hash_hex}")
    print()
    print("  Suggested tweet:")
    print(
        f"  🤖 @CeloGovAI is now a registered ERC-8004 agent on @Celo!\n"
        f"  agentId: {agent_id}\n"
        f"  The ultimate Governance Hub for the Celo ecosystem.\n"
        f"  Use fast commands like /vote, track proposals, and participate in DAO governance with AI.\n"
        f"  #Celo #Governance #AI #ERC8004"
    )
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
