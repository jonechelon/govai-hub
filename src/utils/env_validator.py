# src/utils/env_validator.py
# Celo GovAI Hub — environment variable validator

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # load .env into os.environ at import time

logger = logging.getLogger(__name__)

# Human-readable hints shown when a required variable is missing
_VAR_HINTS: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN":        "Create a bot via @BotFather on Telegram and copy the token",
    "GROQ_API_KEY":              "Get it from https://console.groq.com/keys",
    "ADMIN_CHAT_ID":             "Send a message to @userinfobot on Telegram to find your numeric chat ID",
    "CELO_RPC_URL":              "Default: https://forno.celo.org (or https://rpc.ankr.com/celo)",
    "BOT_WALLET_ADDRESS":        'Generate with: python -c "from web3 import Web3; a=Web3().eth.account.create(); print(a.address)"',
    "BOT_WALLET_PRIVATE_KEY":    "Generated alongside BOT_WALLET_ADDRESS — NEVER commit to Git",
    "GOVERNANCE_DELEGATE_ADDRESS": "The wallet address that receives vote delegations — must match GOVERNANCE_PRIVATE_KEY",
    "GOVERNANCE_PRIVATE_KEY":    "Private key of the governance delegate wallet used by GovernanceExecutor — NEVER commit to Git",
}

# Variables whose value must start with '0x' (Ethereum hex strings)
_HEX_PREFIXED_VARS: frozenset[str] = frozenset({
    "BOT_WALLET_PRIVATE_KEY",
    "GOVERNANCE_PRIVATE_KEY",
})


@dataclass
class Config:
    """Holds all required environment variables for Celo GovAI Hub."""

    telegram_bot_token: str
    admin_chat_id: str
    celo_rpc_url: str
    groq_api_key: str
    bot_wallet_address: str
    bot_wallet_private_key: str
    governance_delegate_address: str
    governance_private_key: str


def load_env_config() -> Config:
    """Load all required environment variables into a Config object.

    Returns:
        Config instance populated from the environment.

    Raises:
        EnvironmentError: if any required variable is missing, empty, or malformed.
    """
    try:
        return Config(
            telegram_bot_token=get_env_or_fail("TELEGRAM_BOT_TOKEN"),
            admin_chat_id=get_env_or_fail("ADMIN_CHAT_ID"),
            celo_rpc_url=get_env_or_fail("CELO_RPC_URL"),
            groq_api_key=get_env_or_fail("GROQ_API_KEY"),
            bot_wallet_address=get_env_or_fail("BOT_WALLET_ADDRESS"),
            bot_wallet_private_key=get_env_or_fail("BOT_WALLET_PRIVATE_KEY"),
            governance_delegate_address=get_env_or_fail("GOVERNANCE_DELEGATE_ADDRESS"),
            governance_private_key=get_env_or_fail("GOVERNANCE_PRIVATE_KEY"),
        )
    except ValueError as exc:
        raise EnvironmentError(str(exc)) from exc


def get_env_or_fail(key: str) -> str:
    """Return the value of a required environment variable.

    For variables listed in _HEX_PREFIXED_VARS the value must start with '0x';
    a startup error is raised otherwise to prevent silent misconfigurations.

    Args:
        key: Name of the environment variable to load.

    Returns:
        The variable's value as a non-empty string.

    Raises:
        ValueError: if the variable is missing, empty, or missing the '0x' prefix.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        hint = _VAR_HINTS.get(key, f"Set {key} in your .env file")
        raise ValueError(
            f"Missing required environment variable: {key}\n"
            f"  → {hint}"
        )
    if key in _HEX_PREFIXED_VARS and not value.startswith("0x"):
        raise ValueError(
            f"Invalid format for environment variable: {key}\n"
            f"  → Value must start with '0x' (got: '{value[:6]}...')"
        )
    return value
