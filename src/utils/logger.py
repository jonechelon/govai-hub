# src/utils/logger.py
# Celo GovAI Hub — central logging (P39: dual mode via LOG_TO_FILE)
#
# Production (Render): LOG_TO_FILE=false — console only (ephemeral filesystem).
# Development (local): LOG_TO_FILE=true — console + RotatingFileHandler to data/logs/.

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from src.utils.paths import LOGS_DIR


def setup_logger(name: str = "celo-govai-hub") -> logging.Logger:
    """Configure and return the main application logger.

    Production: console only (INFO+). Development: console + bot.log (DEBUG+)
    and errors.log (ERROR+). Noisy third-party libs (httpx, telegram, apscheduler)
    are forced to WARNING.
    """
    log_level = getattr(
        logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    log_to_file = os.getenv("LOG_TO_FILE", "true").lower() == "true"

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    # Console handler — always enabled
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handlers — only when LOG_TO_FILE=true (typically local dev)
    if log_to_file:
        log_dir = LOGS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)  # idempotent

        bot_log = log_dir / "bot.log"
        err_log = log_dir / "errors.log"

        # Full log (DEBUG+)
        file_handler = RotatingFileHandler(
            bot_log,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Error-only log (ERROR+)
        error_handler = RotatingFileHandler(
            err_log,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)

    # Silence noisy third-party libraries
    for lib in ("httpx", "telegram", "apscheduler", "httpcore"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # So that logging.getLogger(__name__) in other modules uses the same config
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(console_handler)
    if log_to_file:
        root.addHandler(file_handler)
        root.addHandler(error_handler)

    return logger


# Global application logger — import this in other modules
logger = setup_logger()
