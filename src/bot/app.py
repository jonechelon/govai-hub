# src/bot/app.py
# Celo GovAI Hub — bot application factory and main entrypoint

from __future__ import annotations

import asyncio
import os
import signal
import sys
from urllib.parse import urlparse

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
    delegate_handler,
    revoke_handler,
    govstatus_handler,
    vote_handler,
    proposal_handler,
    govlist_handler,
    govhistory_handler,
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
    governance_handler,
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

    # Start governance poller AFTER scheduler.start()
    scheduler.start_governance_poller(application.bot)

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

    # Remove governance poller job first, if present
    if scheduler.scheduler.get_job("governance_poller"):
        scheduler.scheduler.remove_job("governance_poller")
        logger.info("[SHUTDOWN] Governance poller stopped")

    await scheduler.shutdown()
    logger.info("[SHUTDOWN] Bot stopped gracefully")


def build_application() -> Application:
    """Build and configure the Telegram Application instance.

    Uses ApplicationBuilder with post_init / post_shutdown hooks.
    Registers all command and callback handlers. No business logic — wiring only.

    The Updater is explicitly disabled (.updater(None)) because webhook HTTP
    is handled by our own aiohttp server in run_bot(). PTB's built-in webhook
    server (used by run_webhook()) owns its own aiohttp app and freezes routes
    before we can inject /health — making it impossible to co-host both
    endpoints on the same port without owning the server ourselves.

    Returns:
        Configured Application instance (not yet running).
    """
    # FIXME: TEMPORARY LOCAL POLLING — comment out .updater(None) so run_polling
    # has an Updater. Revert before committing to Render (webhook mode).
    application = (
        ApplicationBuilder()
        .token(get_env_or_fail("TELEGRAM_BOT_TOKEN"))
        # Disable the built-in Updater: webhook HTTP is handled by our own
        # aiohttp server below, so PTB's internal webhook server is not needed.
        # .updater(None)  # TEMPORARY: commented for local polling — uncomment for Render
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
    application.add_handler(CommandHandler("governance", governance_handler))

    # Governance & Voting handlers
    application.add_handler(delegate_handler)
    application.add_handler(revoke_handler)
    application.add_handler(govstatus_handler)
    application.add_handler(vote_handler)
    application.add_handler(proposal_handler)
    application.add_handler(govlist_handler)
    application.add_handler(govhistory_handler)

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

    return application


async def run_bot() -> None:
    """Run the bot using a self-managed aiohttp webhook server.

    Why not Application.run_webhook()?
    PTB's run_webhook() creates an internal aiohttp.web.Application whose routes
    are frozen by AppRunner.setup() before we can inject additional endpoints.
    There is no public hook between WebhookServer.__init__() (where web_app is
    created) and WebhookServer.serve() (where freeze happens). Attempting to add
    routes via application.web_app raises AttributeError; attempting it after
    startup silently fails because the router is frozen.

    By owning the aiohttp server we register both routes atomically before
    setup(), which is the only correct place to do it:
        POST  /{webhook_path}  — Telegram update receiver
        GET   /health          — UptimeRobot / Render health check
        GET   /                — Render connectivity probe (returns 200)

    PTB's update_queue is used to hand off deserialized Update objects to the
    Application's processing loop, started by application.start().

    Lifecycle:
        initialize() → start() → TCPSite.start() → await stop_event
        → site.stop() → runner.cleanup() → stop() → shutdown()
    """
    port = int(os.getenv("PORT", "8080"))

    # WEBHOOK_URL in render.yaml is the full URL including the path,
    # e.g. "https://up-to-celo.onrender.com/telegram".
    webhook_url = os.getenv("WEBHOOK_URL", "").rstrip("/")

    # Derive the aiohttp route path from WEBHOOK_URL so the handler always
    # matches what Telegram was told to POST to. Defaults to "/telegram".
    parsed_path = urlparse(webhook_url).path
    webhook_path = parsed_path if parsed_path and parsed_path != "/" else "/telegram"

    application = build_application()

    # Initialize PTB: sets up the bot object and triggers on_startup (post_init).
    await application.initialize()

    # Start the update-processing background task (consumes from update_queue).
    await application.start()

    # ------------------------------------------------------------------
    # Build our own aiohttp server — we own every route on this server.
    # ------------------------------------------------------------------
    aio_app = web.Application()

    async def telegram_update_handler(request: web.Request) -> web.Response:
        """Receive a Telegram update, deserialize it, and push it to PTB."""
        try:
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.update_queue.put(update)
            logger.debug("[WEBHOOK] Update enqueued | update_id=%s", update.update_id)
        except Exception as exc:
            # Always return 200: a non-200 response causes Telegram to retry the
            # same update endlessly, flooding the queue on malformed payloads.
            logger.warning("[WEBHOOK] Failed to enqueue update: %s", exc)
        return web.Response(status=200)

    async def root_handler(request: web.Request) -> web.Response:
        """Return 200 for Render's default connectivity probe on GET /."""
        return web.Response(text="Celo GovAI Hub OK")

    aio_app.router.add_post(webhook_path, telegram_update_handler)
    aio_app.router.add_get("/health", _health_handler)
    aio_app.router.add_get("/", root_handler)

    # Register the webhook URL with Telegram (idempotent — safe on every restart).
    if webhook_url:
        await application.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=list(Update.ALL_TYPES),
        )
        logger.info("[STARTUP] Webhook registered | url=%s", webhook_url)
    else:
        logger.warning(
            "[STARTUP] WEBHOOK_URL not set — Telegram updates will NOT arrive. "
            "Set WEBHOOK_URL=https://<render-host>/telegram in environment vars."
        )

    # Start the HTTP server — all routes are registered before setup() freezes them.
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(
        "[STARTUP] Webhook server listening | port=%d | path=%s", port, webhook_path
    )
    logger.info("[STARTUP] Health endpoint ready | port=%d | path=/health", port)
    logger.info("[STARTUP] Celo GovAI Hub started | version: 1.1 | mode: webhook")

    # ------------------------------------------------------------------
    # Block until SIGTERM or SIGINT is received (Render sends SIGTERM).
    # ------------------------------------------------------------------
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        logger.info("[SHUTDOWN] Stop signal received — initiating graceful shutdown")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            # asyncio signal handlers are non-blocking and safe on Linux (Render).
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            # Fallback for Windows during local development.
            signal.signal(sig, _request_stop)

    await stop_event.wait()

    # ------------------------------------------------------------------
    # Graceful shutdown: HTTP server first, then PTB.
    # ------------------------------------------------------------------
    logger.info("[SHUTDOWN] Stopping aiohttp server...")
    await site.stop()
    await runner.cleanup()

    logger.info("[SHUTDOWN] Stopping PTB application...")
    await application.stop()
    await application.shutdown()


def main() -> None:
    """Main entrypoint: validate environment, build application, and start webhook server."""
    # Fail fast with a descriptive error if any required var is missing.
    get_env_or_fail("TELEGRAM_BOT_TOKEN")
    get_env_or_fail("GROQ_API_KEY")
    get_env_or_fail("ADMIN_CHAT_ID")
    get_env_or_fail("CELO_RPC_URL")
    get_env_or_fail("BOT_WALLET_ADDRESS")
    get_env_or_fail("BOT_WALLET_PRIVATE_KEY")

    bot_name = CONFIG.get("bot", {}).get("name", "Celo GovAI Hub")
    digest_time = CONFIG.get("digest_schedule", {}).get("time", "08:30")
    digest_tz = CONFIG.get("digest_schedule", {}).get("timezone", "Europe/Madrid")
    logger.info(
        f"[CONFIG] Loaded | bot={bot_name} | digest={digest_time} {digest_tz}"
    )

    try:
        # -------------------------------------------------------------------------
        # FIXME: TEMPORARY LOCAL POLLING — revert before committing to Render.
        # Use run_polling when no tunnel (ngrok) is available locally.
        # Production (Render) uses asyncio.run(run_bot()) with webhook + aiohttp.
        # -------------------------------------------------------------------------
        # asyncio.run(run_bot())
        application = build_application()
        application.run_polling(drop_pending_updates=True)
        logger.info("[SHUTDOWN] Polling stopped — exiting cleanly")
        sys.exit(0)

    except KeyboardInterrupt:
        # Ctrl+C during local development — stop_event handles it via SIGINT handler,
        # but asyncio.run() itself may raise KeyboardInterrupt on some platforms.
        logger.info("[SHUTDOWN] KeyboardInterrupt — exiting cleanly")
        sys.exit(0)

    except Exception as exc:
        logger.exception(f"[FATAL] Unhandled exception: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
