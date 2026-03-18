# src/utils/config_loader.py
# Celo GovAI Hub — YAML configuration loader with validation

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS: list[str] = [
    "bot.name",
    "digest_schedule.time",
    "digest_schedule.timezone",
    "apps_available",
    "payment.cusd_contract",
]

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


def _get_nested(data: dict, dotted_key: str) -> Any:
    """Retrieve a value from a nested dict using a dot-separated key.

    Args:
        data: the dictionary to traverse.
        dotted_key: e.g. "digest_schedule.time".

    Returns:
        The value at the given path, or None if not found.
    """
    keys = dotted_key.split(".")
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def load_config(path: Path = _CONFIG_PATH) -> dict:
    """Load and validate the YAML configuration file.

    Args:
        path: path to config.yaml (defaults to project root).

    Returns:
        Parsed configuration as a dict.

    Raises:
        FileNotFoundError: if config.yaml does not exist.
        ValueError: if any required field is missing.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {path}. "
            "Make sure you are running from the project root."
        )

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        raise ValueError("config.yaml must be a YAML mapping (dict) at the top level.")

    for field in _REQUIRED_FIELDS:
        value = _get_nested(data, field)
        if value is None:
            raise ValueError(f"config.yaml missing required field: {field}")

    logger.debug("[CONFIG] config.yaml loaded from %s", path)
    return data


# Singleton — imported once, shared across modules
CONFIG: dict = load_config()
