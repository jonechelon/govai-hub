# src/utils/env_validator.py
# Up-to-Celo — environment variable loader and validator

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class AppConfig:
    telegram_bot_token: str
    groq_api_key: str
    admin_chat_id: int
    celo_rpc_url: str
    nitter_instance: str
    coingecko_api_key: str


def get_env_or_fail() -> AppConfig:
    """Load and validate all environment variables.

    Returns:
        AppConfig: validated configuration object.

    Raises:
        EnvironmentError: if any required variable is missing or empty.
    """
    load_dotenv()

    # --- required variables ---
    telegram_bot_token = _require(
        "TELEGRAM_BOT_TOKEN",
        "Create a bot via @BotFather on Telegram and copy the token.",
    )
    groq_api_key = _require(
        "GROQ_API_KEY",
        "Get your API key at https://console.groq.com/keys",
    )
    admin_chat_id_raw = _require(
        "ADMIN_CHAT_ID",
        "Send a message to @userinfobot on Telegram to find your numeric chat ID.",
    )

    # ADMIN_CHAT_ID must be a valid integer
    try:
        admin_chat_id = int(admin_chat_id_raw)
    except ValueError:
        raise EnvironmentError(
            f"ADMIN_CHAT_ID must be a numeric Telegram chat ID, got: '{admin_chat_id_raw}'. "
            "Send a message to @userinfobot on Telegram to find your numeric chat ID."
        )

    # --- optional variables ---
    celo_rpc_url = os.getenv("CELO_RPC_URL", "").strip() or "https://forno.celo.org"
    nitter_instance = os.getenv("NITTER_INSTANCE", "").strip() or "https://nitter.net"
    coingecko_api_key = os.getenv("COINGECKO_API_KEY", "").strip()

    return AppConfig(
        telegram_bot_token=telegram_bot_token,
        groq_api_key=groq_api_key,
        admin_chat_id=admin_chat_id,
        celo_rpc_url=celo_rpc_url,
        nitter_instance=nitter_instance,
        coingecko_api_key=coingecko_api_key,
    )


def _require(var: str, instructions: str) -> str:
    """Return the value of a required env variable or raise EnvironmentError.

    Args:
        var: environment variable name.
        instructions: human-readable instructions for obtaining the value.

    Raises:
        EnvironmentError: if the variable is missing or empty.
    """
    value = os.getenv(var, "").strip()
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {var}\n"
            f"  → {instructions}\n"
            f"  → Add it to your .env file: {var}=your_value_here"
        )
    return value
