"""
scripts/registeragent.py
------------------------
Standalone script to register or update GovAI Hub as ERC-8004 agent on Celo Mainnet.

Usage:
  python scripts/registeragent.py             # register new agent
  python scripts/registeragent.py --dry-run   # validate sem broadcast
  python scripts/registeragent.py --update-uri # atualiza tokenURI do agentId existente
  python scripts/registeragent.py --check      # lê tokenURI on-chain atual

Requirements:
  .env      → BOT_WALLET_ADDRESS, BOT_WALLET_PRIVATE_KEY
  config.yaml → erc8004.identity_registry, agent_name, agent_description,
                agent_endpoint, render_base_url, chain_id
  Wallet must hold >= 0.01 CELO for gas.

Output:
  data/agentregistration.json → {agentId, txHash, wallet, registry, timestamp, celoscanUrl, agentURI}
"""

import argparse
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
# Logging
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
MIN_BALANCE_CELO = 0.01
GAS_SAFETY_MARGIN = 1.2

IDENTITY_REGISTRY_ABI = [
    {
        "inputs": [{"internalType": "string", "name": "agentURI", "type": "string"}],
        "name": "register",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "agentURI", "type": "string"},
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
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"indexed": False, "internalType": "string", "name": "agentURI", "type": "string"},
            {"indexed": True, "internalType": "address", "name": "owner", "type": "address"},
        ],
        "name": "Registered",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"indexed": False, "internalType": "string", "name": "agentURI", "type": "string"},
        ],
        "name": "URIUpdated",
        "type": "event",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        logger.error("config.yaml not found at %s", CONFIG_FILE)
        sys.exit(1)
    with CONFIG_FILE.open() as fh:
        return yaml.safe_load(fh)


def build_agent_card(cfg: dict, agent_id: int | str = "") -> dict:
    """
    Build ERC-8004 compliant agent card JSON.
    Uses 'endpoints' (not 'services') per official spec.
    Includes 'registrations' array with agentRegistry CAIP-2 identifier.
    """
    erc = cfg["erc8004"]
    registry = erc.get("identity_registry", "")
    return {
        "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
        "name": erc["agent_name"],
        "description": erc["agent_description"],
        "endpoints": [
            {
                "name": "telegram",
                "endpoint": erc["agent_endpoint"],
                "version": "1.0.0",
                "capabilities": {},
            }
        ],
        "x402Support": False,
        "active": True,
        "registrations": [
            {
                "agentId": agent_id,
                "agentRegistry": f"eip155:{CELO_CHAIN_ID}:{registry}",
            }
        ],
    }


def get_agent_uri(cfg: dict) -> str:
    """
    Return the public HTTPS URI where the agent card JSON is served.
    This URL must be accessible before calling register() or setAgentURI().
    Route expected: GET {render_base_url}/.well-known/agent-registration.json
    """
    render_base = cfg["erc8004"].get("render_base_url", "").rstrip("/")
    if not render_base or "TODO" in render_base.upper():
        logger.error(
            "ERC8004: 'render_base_url' not set in config.yaml → erc8004.\n"
            "  Set it to your Render deploy URL, e.g.:\n"
            "  render_base_url: https://govai-hub.onrender.com"
        )
        sys.exit(1)
    uri = f"{render_base}/.well-known/agent-registration.json"
    logger.info("ERC8004: Agent URI → %s", uri)
    return uri


def connect_rpc(cfg: dict) -> Web3:
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
    logger.error("ERC8004: All RPC endpoints failed.")
    sys.exit(1)


def validate_registry(w3: Web3, address: str) -> str:
    if not address or "TODO" in address.upper():
        logger.error("ERC8004: identity_registry is still a placeholder: %s", address)
        sys.exit(1)
    try:
        checksum = Web3.to_checksum_address(address)
    except ValueError:
        logger.error("ERC8004: Invalid identity_registry address: %s", address)
        sys.exit(1)
    code = w3.eth.get_code(checksum)
    if code in (b"", "0x"):
        logger.error("ERC8004: No bytecode at %s — wrong address?", checksum)
        sys.exit(1)
    logger.info("ERC8004: Registry bytecode confirmed at %s", checksum)
    return checksum


def check_balance(w3: Web3, address: str) -> None:
    balance_celo = float(w3.from_wei(w3.eth.get_balance(address), "ether"))
    logger.info("ERC8004: Wallet balance = %.6f CELO", balance_celo)
    if balance_celo < MIN_BALANCE_CELO:
        logger.error(
            "ERC8004: Insufficient balance (%.6f CELO). Need >= %.4f CELO for gas.",
            balance_celo,
            MIN_BALANCE_CELO,
        )
        sys.exit(1)


def load_existing_registration() -> dict | None:
    if OUTPUT_FILE.exists():
        with OUTPUT_FILE.open() as fh:
            return json.load(fh)
    return None


def save_registration(data: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w") as fh:
        json.dump(data, fh, indent=2)
    logger.info("ERC8004: Registration saved to %s", OUTPUT_FILE)


def send_tx(w3: Web3, contract_fn, wallet_checksum: str, private_key: str) -> dict:
    """Build, sign, broadcast and wait for receipt. Returns receipt dict."""
    nonce = w3.eth.get_transaction_count(wallet_checksum, "pending")
    gas_price = w3.eth.gas_price
    try:
        gas_estimate = contract_fn.estimate_gas({"from": wallet_checksum})
        gas_limit = int(gas_estimate * GAS_SAFETY_MARGIN)
        logger.info("ERC8004: Gas estimated=%d → limit=%d", gas_estimate, gas_limit)
    except ContractLogicError as exc:
        logger.error("ERC8004: Contract reverted during gas estimation: %s", exc)
        sys.exit(1)

    tx = contract_fn.build_transaction(
        {
            "chainId": CELO_CHAIN_ID,
            "from": wallet_checksum,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }
    )
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = tx_hash.hex()
    logger.info("ERC8004: Tx broadcast — %s", tx_hash_hex)

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        logger.error("ERC8004: Tx reverted. Check https://celoscan.io/tx/%s", tx_hash_hex)
        sys.exit(1)
    return receipt, tx_hash_hex


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Register/update GovAI Hub as ERC-8004 agent")
    parser.add_argument("--dry-run", action="store_true", help="Validate without broadcasting tx")
    parser.add_argument("--update-uri", action="store_true", help="Call setAgentURI() for existing agentId")
    parser.add_argument("--check", action="store_true", help="Read tokenURI on-chain and exit (no tx)")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    wallet_address = os.getenv("BOT_WALLET_ADDRESS", "").strip()
    private_key = os.getenv("BOT_WALLET_PRIVATE_KEY", "").strip()

    if not wallet_address or not private_key:
        logger.error("ERC8004: BOT_WALLET_ADDRESS and BOT_WALLET_PRIVATE_KEY must be set in .env")
        sys.exit(1)
    if not private_key.startswith("0x"):
        logger.error("ERC8004: BOT_WALLET_PRIVATE_KEY must start with '0x'")
        sys.exit(1)

    cfg = load_config()
    erc = cfg.get("erc8004", {})
    if not erc:
        logger.error("ERC8004: Missing 'erc8004' section in config.yaml")
        sys.exit(1)

    registry_address_raw: str = erc.get("identity_registry", "")
    existing = load_existing_registration()

    w3 = connect_rpc(cfg)
    wallet_checksum = Web3.to_checksum_address(wallet_address)
    registry_checksum = validate_registry(w3, registry_address_raw)
    contract = w3.eth.contract(address=registry_checksum, abi=IDENTITY_REGISTRY_ABI)

    # ── MODE: --check ──────────────────────────────────────────────────────
    if args.check:
        agent_id = existing["agentId"] if existing else int(erc.get("agent_id", 0))
        try:
            current_uri = contract.functions.tokenURI(agent_id).call()
            owner = contract.functions.ownerOf(agent_id).call()
            print(f"\n{'=' * 60}")
            print(f"  agentId  : {agent_id}")
            print(f"  owner    : {owner}")
            print(f"  tokenURI : {current_uri or '(EMPTY — not set)'}")
            print(f"  celoscan : https://celoscan.io/token/{registry_checksum}?a={agent_id}")
            print(f"{'=' * 60}\n")
        except Exception as exc:
            logger.error("ERC8004: Failed to read tokenURI(%s): %s", agent_id, exc)
        return

    # ── MODE: --update-uri ─────────────────────────────────────────────────
    if args.update_uri:
        if not existing:
            logger.error(
                "ERC8004: data/agentregistration.json not found.\n"
                "  Run without --update-uri to register first."
            )
            sys.exit(1)

        agent_id = existing["agentId"]
        agent_uri = get_agent_uri(cfg)
        card = build_agent_card(cfg, agent_id)
        logger.info("ERC8004: Agent card preview:\n%s", json.dumps(card, indent=2))

        if args.dry_run:
            logger.info("ERC8004: --dry-run passed. No transaction sent.")
            logger.info("  Would call setAgentURI(%s, %s)", agent_id, agent_uri)
            return

        check_balance(w3, wallet_checksum)
        receipt, tx_hash_hex = send_tx(
            w3,
            contract.functions.setAgentURI(agent_id, agent_uri),
            wallet_checksum,
            private_key,
        )

        existing["agentURI"] = agent_uri
        existing["uriUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        existing["uriUpdateTxHash"] = tx_hash_hex
        save_registration(existing)

        print(f"\n{'=' * 60}")
        print(f"  URI updated for agentId: {agent_id}")
        print(f"  New URI  : {agent_uri}")
        print(f"  Tx       : https://celoscan.io/tx/{tx_hash_hex}")
        print(f"  Explorer : https://8004scan.io/agent/{agent_id}")
        print(f"{'=' * 60}\n")
        return

    # ── MODE: register (default) ───────────────────────────────────────────
    if existing:
        logger.info(
            "ERC8004: Agent already registered — agentId=%s tx=%s.\n"
            "  Use --update-uri to update tokenURI or --check to inspect on-chain state.\n"
            "  Delete %s to re-register (not recommended).",
            existing.get("agentId"),
            existing.get("txHash"),
            OUTPUT_FILE,
        )
        return

    agent_uri = get_agent_uri(cfg)
    card = build_agent_card(cfg)
    logger.info("ERC8004: Agent card preview:\n%s", json.dumps(card, indent=2))

    if args.dry_run:
        logger.info("ERC8004: --dry-run passed. No transaction sent.")
        logger.info("  Wallet   : %s", wallet_checksum)
        logger.info("  Registry : %s", registry_checksum)
        logger.info("  Agent URI: %s", agent_uri)
        return

    check_balance(w3, wallet_checksum)
    receipt, tx_hash_hex = send_tx(
        w3,
        contract.functions.register(agent_uri),
        wallet_checksum,
        private_key,
    )

    agent_id: int | str = "unknown"
    try:
        logs = contract.events.Registered().process_receipt(receipt)
        if logs:
            agent_id = logs[0]["args"]["agentId"]
    except Exception as exc:
        logger.warning("ERC8004: Could not parse Registered event: %s", exc)

    registration = {
        "agentId": agent_id,
        "txHash": tx_hash_hex,
        "wallet": wallet_checksum,
        "registry": registry_checksum,
        "agentURI": agent_uri,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "celoscanUrl": f"https://celoscan.io/tx/{tx_hash_hex}",
    }
    save_registration(registration)

    print(f"\n{'=' * 60}")
    print("  ERC-8004 registration complete!")
    print(f"  agentId  : {agent_id}")
    print(f"  txHash   : {tx_hash_hex}")
    print(f"  celoscan : https://celoscan.io/tx/{tx_hash_hex}")
    print(f"  Explorer : https://8004scan.io/agent/{agent_id}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
