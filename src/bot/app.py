# src/bot/app.py
# Up-to-Celo — bot application factory and main entrypoint (P5)

from __future__ import annotations

import logging
import signal
import sys

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, MessageHandler, filters

from src.bot.callbacks import callback_query_handler
from src.bot.handlers import (
    ask_handler,
    confirm_payment_handler,
    digest_handler,
    free_text_handler,
    help_handler,
    inline_handler,
    premium_handler,
    settings_handler,
    start_handler,
    status_handler,
    stop_handler,
    subscribe_handler,
    unsubscribe_handler,
)
from src.database.manager import DatabaseManager
from src.scheduler.scheduler import scheduler
from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail
from src.utils.logger import setup_logger

logger = logging.getLogger(__name__)

# Singleton DB instance for lifecycle hooks
db = DatabaseManager()


async def on_startup(application: Application) -> None:
    """Run once after the Application is initialized.

    Order: init_db (tables ready) → scheduler.start() → downgrade_expired_users().
    """
    await db.init_db()
    await scheduler.start(application)
    await db.downgrade_expired_users()
    logger.info("[STARTUP] DB initialized, scheduler started, expired users downgraded")


async def on_shutdown(application: Application) -> None:
    """Run once after the Application has stopped."""
    await scheduler.shutdown()
    logger.info("[SHUTDOWN] Up-to-Celo bot shutting down")


def build_application() -> Application:
    """Build and configure the Telegram Application instance.

    Uses ApplicationBuilder with post_init / post_shutdown hooks.
    Registers all command and callback handlers. No business logic — wiring only.

    Returns:
        Configured Application instance (not yet running).
    """
    application = (
        ApplicationBuilder()
        .token(get_env_or_fail("TELEGRAM_BOT_TOKEN"))
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # Command handlers (instances from src.bot.handlers)
    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(status_handler)
    application.add_handler(premium_handler)
    application.add_handler(confirm_payment_handler)
    application.add_handler(digest_handler)
    application.add_handler(settings_handler)
    application.add_handler(subscribe_handler)
    application.add_handler(unsubscribe_handler)
    application.add_handler(ask_handler)
    application.add_handler(stop_handler)

    # Callback query router (inline keyboards)
    application.add_handler(callback_query_handler)

    # Inline query handler (BotFather → Inline Mode must be enabled)
    application.add_handler(inline_handler)

    # Free-text handler: only active when ask session exists (checked inside handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler)
    )

    return application


def main() -> None:
    """Main entrypoint: validate environment, build application, and start polling."""
    # Step 1: initialize logging before anything else
    setup_logger()

    # Step 2: validate all required environment variables
    # Fails fast with descriptive error if any var is missing
    get_env_or_fail("TELEGRAM_BOT_TOKEN")
    get_env_or_fail("GROQ_API_KEY")
    get_env_or_fail("ADMIN_CHAT_ID")
    get_env_or_fail("CELO_RPC_URL")
    get_env_or_fail("BOT_WALLET_ADDRESS")
    get_env_or_fail("BOT_WALLET_PRIVATE_KEY")

    # Step 3: confirm config loaded successfully
    bot_name = CONFIG.get("bot", {}).get("name", "Up-to-Celo")
    digest_time = CONFIG.get("digest_schedule", {}).get("time", "08:30")
    digest_tz = CONFIG.get("digest_schedule", {}).get("timezone", "Europe/Madrid")
    logger.info(
        f"[CONFIG] Loaded | bot={bot_name} | digest={digest_time} {digest_tz}"
    )

    # Step 4: handle SIGTERM gracefully (Render sends SIGTERM on worker stop)
    def _handle_sigterm(signum, frame) -> None:
        logger.info("[SHUTDOWN] SIGTERM received — stopping bot")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Step 5 + 6: build app and log startup
    try:
        application = build_application()
        logger.info(
            "[STARTUP] Up-to-Celo bot started | version: 1.1 | webhook: polling"
        )

        # Step 7: start polling — blocks until stopped
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        # Step 8: clean exit after run_polling returns (e.g. KeyboardInterrupt handled internally)
        logger.info("[SHUTDOWN] Polling stopped — exiting cleanly")
        sys.exit(0)

    except KeyboardInterrupt:
        # Ctrl+C during local development
        logger.info("[SHUTDOWN] KeyboardInterrupt — exiting cleanly")
        sys.exit(0)

    except Exception as exc:
        # Step 9: any fatal error during startup or polling
        logger.exception(f"[FATAL] Unhandled exception: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
