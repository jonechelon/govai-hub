# src/utils/env_validator.py
# Up-to-Celo — environment variable validator

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # load .env into os.environ at import time

logger = logging.getLogger(__name__)

# Human-readable hints shown when a required variable is missing
_VAR_HINTS: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN":     "Create a bot via @BotFather on Telegram and copy the token",
    "GROQ_API_KEY":           "Get it from https://console.groq.com/keys",
    "ADMIN_CHAT_ID":          "Send a message to @userinfobot on Telegram to find your numeric chat ID",
    "CELO_RPC_URL":           "Default: https://forno.celo.org (or https://rpc.ankr.com/celo)",
    "BOT_WALLET_ADDRESS":     'Generate with: python -c "from web3 import Web3; a=Web3().eth.account.create(); print(a.address)"',
    "BOT_WALLET_PRIVATE_KEY": "Generated alongside BOT_WALLET_ADDRESS — NEVER commit to Git",
}


@dataclass
class Config:
    """Holds all required environment variables for Up-to-Celo."""

    telegram_bot_token: str
    admin_chat_id: str
    celo_rpc_url: str
    groq_api_key: str
    bot_wallet_address: str
    bot_wallet_private_key: str


def load_env_config() -> Config:
    """Load all required environment variables into a Config object.

    Returns:
        Config instance populated from the environment.

    Raises:
        EnvironmentError: if any required variable is missing or empty.
    """
    try:
        return Config(
            telegram_bot_token=get_env_or_fail("TELEGRAM_BOT_TOKEN"),
            admin_chat_id=get_env_or_fail("ADMIN_CHAT_ID"),
            celo_rpc_url=get_env_or_fail("CELO_RPC_URL"),
            groq_api_key=get_env_or_fail("GROQ_API_KEY"),
            bot_wallet_address=get_env_or_fail("BOT_WALLET_ADDRESS"),
            bot_wallet_private_key=get_env_or_fail("BOT_WALLET_PRIVATE_KEY"),
        )
    except ValueError as exc:
        raise EnvironmentError(str(exc)) from exc


def get_env_or_fail(key: str) -> str:
    """Return the value of a required environment variable.

    Args:
        key: Name of the environment variable to load.

    Returns:
        The variable's value as a non-empty string.

    Raises:
        ValueError: if the variable is missing or empty, with a descriptive hint.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        hint = _VAR_HINTS.get(key, f"Set {key} in your .env file")
        raise ValueError(
            f"Missing required environment variable: {key}\n"
            f"  → {hint}"
        )
    return value
