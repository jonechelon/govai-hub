# src/utils/logger.py
# Up-to-Celo — logging setup (minimal until P39: RotatingFileHandler + errors.log)

from __future__ import annotations

import logging
import os


def setup_logger() -> None:
    """Configure root logger for the application.

    Minimal setup: console at INFO. P39 will add RotatingFileHandler
    for data/logs/bot.log and errors.log.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Suppress noisy libraries until P39
    for name in ("httpx", "telegram", "apscheduler", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
