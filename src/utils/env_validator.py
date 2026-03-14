# src/utils/env_validator.py
# Up-to-Celo — environment variable validator

from __future__ import annotations

import logging
import os

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
