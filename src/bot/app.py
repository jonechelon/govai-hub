# src/bot/app.py
# Up-to-Celo — bot application factory and main entrypoint

from __future__ import annotations

import logging
import sys

from telegram import Bot
from telegram.error import InvalidToken, NetworkError
from telegram.ext import Application, ApplicationBuilder

from src.utils.env_validator import AppConfig, get_env_or_fail

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure root logger for startup (console only, INFO level).
    Full logging setup with file rotation will be added in P40.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _mask_token(token: str) -> str:
    """Return token with all but last 6 characters masked."""
    return f"***{token[-6:]}"


def create_application(config: AppConfig) -> Application:
    """Build and configure the Telegram Application instance.

    Registers command and callback handlers.
    Configures post_init for DB and scheduler initialization.

    Args:
        config: validated AppConfig from get_env_or_fail().

    Returns:
        Configured Application instance (not yet running).
    """
    application = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .build()
    )

    # --- register handlers ---
    # Handlers are stubs until P5–P9 are implemented.
    _register_handlers(application)

    # --- post_init: runs after application.initialize() ---
    # DB init (P23) and scheduler start (P20) will be wired here.
    async def post_init(app: Application) -> None:
        await _on_startup(app, config)

    application.post_init = post_init

    # --- post_shutdown: runs after application stops ---
    async def post_shutdown(app: Application) -> None:
        await _on_shutdown(app)

    application.post_shutdown = post_shutdown

    return application


def _register_handlers(application: Application) -> None:
    """Register all command and callback handlers.

    Stubs — full implementation in P5–P9.
    Each handler will be imported from src/bot/handlers.py and
    src/bot/callbacks.py when those modules are created.
    """
    # Placeholder: no handlers registered yet.
    # P5  → /start, /help
    # P6  → /help (set_my_commands)
    # P7  → /subscribe, /unsubscribe
    # P9  → CallbackQueryHandler (digest inline buttons)
    # P10 → error handler
    logger.debug("Handler registration: stubs in place — awaiting P5–P9.")


async def _on_startup(application: Application, config: AppConfig) -> None:
    """Run once after the Application is initialized.

    Wires: DB init (P23), scheduler start (P20), admin notification.
    """
    bot: Bot = application.bot

    # Validate token with a live API call
    bot_info = await bot.get_me()

    logger.info(
        "[STARTUP] Up-to-Celo Bot started | "
        "username: @%s | id: %d | token: %s",
        bot_info.username,
        bot_info.id,
        _mask_token(config.telegram_bot_token),
    )

    # DB init stub — will call DatabaseManager.init_db() in P23
    logger.info("[STARTUP] Database: stub — awaiting P23.")

    # Scheduler stub — will call DigestScheduler.start() in P20
    logger.info("[STARTUP] Scheduler: stub — awaiting P20.")

    # Notify admin
    try:
        await bot.send_message(
            chat_id=config.admin_chat_id,
            text=(
                "🟡 *Up\\-to\\-Celo started*\n"
                f"Bot: @{bot_info.username}\n"
                f"Token: `{_mask_token(config.telegram_bot_token)}`"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as exc:
        logger.warning("[STARTUP] Could not notify admin: %s", exc)


async def _on_shutdown(application: Application) -> None:
    """Run once after the Application has stopped.

    Wires: scheduler shutdown (P20), DB flush (P23), log flush.
    """
    logger.info("[SHUTDOWN] Scheduler: stub — awaiting P20.")
    logger.info("[SHUTDOWN] Database: stub — awaiting P23.")
    logger.info("[SHUTDOWN] Up-to-Celo Bot stopped gracefully.")


def main() -> None:
    """Main entrypoint: load config, build app, run polling.

    run_polling() in PTB v21+ manages its own event loop internally.
    It must NOT be called inside asyncio.run() or any running coroutine.
    """
    _configure_logging()

    try:
        config = get_env_or_fail()
    except EnvironmentError as exc:
        logger.critical("[STARTUP] Environment error: %s", exc)
        sys.exit(1)

    try:
        application = create_application(config)
    except InvalidToken:
        logger.critical(
            "[STARTUP] InvalidToken — check TELEGRAM_BOT_TOKEN in .env "
            "(get it from @BotFather on Telegram)."
        )
        sys.exit(1)
    except NetworkError as exc:
        logger.critical(
            "[STARTUP] NetworkError during application build: %s — "
            "check your internet connection.", exc
        )
        sys.exit(1)

    logger.info("[STARTUP] Starting polling...")

    # run_polling() creates and manages the event loop itself in PTB v21+.
    # Calling it inside asyncio.run() causes "event loop already running".
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("[SHUTDOWN] KeyboardInterrupt received — exiting.")
        sys.exit(0)
