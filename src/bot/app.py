# src/bot/app.py
# Up-to-Celo — bot application factory and main entrypoint (P5)

from __future__ import annotations

import os
import signal
import sys

from aiohttp import web

from src.utils.logger import logger
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.ext import ContextTypes

from src.bot.callbacks import callback_query_handler
from src.bot.handlers import (
    admin_stats_handler,
    admin_broadcast_handler,
    admin_digest_now_handler,
    ask_handler,
    confirm_payment_handler,
    digest_handler,
    free_text_handler,
    help_handler,
    inline_handler,
    premium_handler,
    settings_handler,
    setwallet_handler,
    start_handler,
    status_handler,
    stop_handler,
    subscribe_handler,
    unsubscribe_handler,
)
from src.database.manager import DatabaseManager
from src.scheduler.scheduler import scheduler
from src.utils.cache_manager import cache as cache_manager
from src.utils.config_loader import CONFIG
from src.utils.env_validator import get_env_or_fail
from src.utils.health_check import HealthChecker, _health_handler

# Singleton DB instance for lifecycle hooks
db = DatabaseManager()


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all unhandled exceptions and notify the user when possible."""
    logger.error(
        "[ERROR] Unhandled exception | update=%s | error=%s",
        update,
        context.error,
        exc_info=context.error,
    )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Something went wrong. Please try again."
            )
        except Exception:
            pass


async def on_startup(application: Application) -> None:
    """Run once after the Application is initialized.

    Order: init_db → downgrade_expired_users → scheduler.start() → health checker.
    Stores uptime_start for /admin_stats and health check.
    """
    from datetime import datetime, timezone

    await db.init_db()
    await db.downgrade_expired_users()
    await scheduler.start(application, db=db)

    # Standalone aiohttp health server is disabled in webhook mode because the
    # PTB webhook server already binds to the same PORT. The /health endpoint
    # is now exposed by the webhook aiohttp application instead (see
    # build_application()) to avoid running two HTTP servers on port 8080.

    startup_time = datetime.now(timezone.utc)
    application.bot_data["uptime_start"] = startup_time

    # Start cache cleanup background task (runs every hour)
    cache_manager.start_cleanup_task()

    health_checker = HealthChecker(
        db=db, bot=application.bot, start_time=startup_time
    )
    health_checker.start()
    application.bot_data["health_checker"] = health_checker

    logger.info(
        "[STARTUP] DB initialized, scheduler started, "
        "cache cleanup started, health checker started"
    )


async def on_shutdown(application: Application) -> None:
    """Run once after the Application has stopped."""
    checker = application.bot_data.get("health_checker")
    if checker:
        checker.stop()
    cache_manager.stop()
    await scheduler.shutdown()
    logger.info("[SHUTDOWN] Bot stopped gracefully")


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
    application.add_handler(setwallet_handler)
    application.add_handler(digest_handler)
    application.add_handler(settings_handler)
    application.add_handler(subscribe_handler)
    application.add_handler(unsubscribe_handler)
    application.add_handler(ask_handler)
    application.add_handler(stop_handler)

    # Admin-only commands (ADMIN_CHAT_ID)
    application.add_handler(CommandHandler("admin_stats", admin_stats_handler))
    application.add_handler(CommandHandler("admin_broadcast", admin_broadcast_handler))
    application.add_handler(CommandHandler("admin_digest_now", admin_digest_now_handler))

    # Callback query router (inline keyboards)
    application.add_handler(callback_query_handler)

    # Inline query handler (BotFather → Inline Mode must be enabled)
    application.add_handler(inline_handler)

    # Free-text handler: only active when ask session exists (checked inside handler)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler)
    )

    # Global error handler — catches all unhandled exceptions and logs them
    application.add_error_handler(global_error_handler)

    # Expose GET /health on the same aiohttp application used by the PTB
    # webhook server so that UptimeRobot can monitor the bot without a
    # separate aiohttp server binding to port 8080.
    try:
        application.web_app.add_routes([web.get("/health", _health_handler)])
        logger.info("[HEALTH] /health endpoint registered on webhook server")
    except Exception as exc:
        logger.warning(
            "[HEALTH] Failed to register /health route on webhook server: %s", exc
        )

    return application


def main() -> None:
    """Main entrypoint: validate environment, build application, and start polling."""
    # Step 1: logger already initialized on import (src.utils.logger)

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
            "[STARTUP] Up-to-Celo bot started | version: 1.1 | mode: webhook"
        )

        # Step 7: start webhook server — blocks until stopped
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 10000)),
            url_path="telegram",
            webhook_url=os.getenv("WEBHOOK_URL"),
            drop_pending_updates=True,
        )

        # Step 8: clean exit after run_webhook returns (e.g. KeyboardInterrupt handled internally)
        logger.info("[SHUTDOWN] Webhook server stopped — exiting cleanly")
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
