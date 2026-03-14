# src/database/manager.py
# Up-to-Celo — DatabaseManager singleton (async, SQLAlchemy + aiosqlite)

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models import Base, User, UserAppFilter

logger = logging.getLogger(__name__)

_DB_URL = "sqlite+aiosqlite:///data/uptocelo.db"

# All app keys for settings (must match keyboards.APPS_BY_CATEGORY). Used when user has no prefs.
_ALL_APP_KEYS = [
    "MiniPay", "Valora", "HaloFi", "Hurupay",
    "Ubeswap", "Moola", "Mento", "Symmetric", "Mobius", "Knox", "Equalizer", "Uniswap",
    "CeloCashflow", "Unipos",
    "OctoPlace", "Hypermove", "TrueFeedBack",
    "Toucan", "ToucanReFi",
    "ImpactMarket", "Masa",
    "CeloNetwork", "CeloReserve",
]


class DatabaseManager:
    """Async database manager — create once and reuse across the application.

    Usage:
        db = DatabaseManager()
        await db.init_db()
        user = await db.get_or_create_user(telegram_id, username, first_name)
    """

    _instance: Optional[DatabaseManager] = None

    def __new__(cls) -> DatabaseManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._engine = create_async_engine(_DB_URL, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._initialized = True

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def init_db(self) -> None:
        """Create all tables if they do not exist.

        Safe to call on every startup — does not drop existing data.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[DB] Tables created / verified OK")

    async def close(self) -> None:
        """Dispose engine and release connections."""
        await self._engine.dispose()
        logger.info("[DB] Engine disposed")

    # ── user management ────────────────────────────────────────────────────────

    async def get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
    ) -> User:
        """Return existing user or create a new one.

        Args:
            telegram_id: Telegram numeric user ID.
            username: Telegram @username (may be None).
            first_name: user's first name.

        Returns:
            User ORM object.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                logger.info("[DB] New user created | telegram_id: %d | username: %s", telegram_id, username)
            else:
                # Update mutable fields
                user.username = username
                user.first_name = first_name
                user.last_active_at = datetime.now(tz=timezone.utc)
                await session.commit()

            return user

    async def update_subscription(self, telegram_id: int, subscribed: bool) -> None:
        """Enable or disable digest subscription for a user.

        Args:
            telegram_id: Telegram numeric user ID.
            subscribed: True to subscribe, False to unsubscribe.
        """
        async with self._session_factory() as session:
            await session.execute(
                update(User)
                .where(User.telegram_id == telegram_id)
                .values(subscribed=subscribed)
            )
            await session.commit()
        logger.info("[DB] User %d subscription → %s", telegram_id, subscribed)

    async def get_subscribers(self) -> list[User]:
        """Return all users with subscribed=True.

        Returns:
            List of subscribed User objects.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(User.subscribed == True)  # noqa: E712
            )
            return list(result.scalars().all())

    # ── user app filters (P24 / settings) ───────────────────────────────────

    async def get_user_apps(self, user_id: int) -> list[str]:
        """Return list of app keys enabled for the user.

        Default is all apps enabled. Only rows in user_app_filters are overrides
        (disabled = row with enabled=False). So result = all apps minus disabled set.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            List of app_key strings that are enabled.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAppFilter).where(UserAppFilter.telegram_id == user_id)
            )
            rows = result.scalars().all()
        disabled = {r.app_key for r in rows if not r.enabled}
        return [app for app in _ALL_APP_KEYS if app not in disabled]

    async def count_enabled_apps(self, user_id: int) -> int:
        """Return number of apps currently enabled for the user."""
        return len(await self.get_user_apps(user_id))

    async def update_user_app(
        self, user_id: int, app_key: str, enabled: bool
    ) -> None:
        """Set or update one app's enabled state for the user.

        Inserts or updates a row in user_app_filters. If no row exists, creates one.

        Args:
            user_id: Telegram numeric user ID.
            app_key: App identifier (e.g. MiniPay, Valora).
            enabled: True to enable, False to disable.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(UserAppFilter).where(
                    UserAppFilter.telegram_id == user_id,
                    UserAppFilter.app_key == app_key,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    UserAppFilter(
                        telegram_id=user_id,
                        app_key=app_key,
                        enabled=enabled,
                    )
                )
            else:
                row.enabled = enabled
            await session.commit()
        logger.debug("[DB] User %d app %s → enabled=%s", user_id, app_key, enabled)

    # ── premium subscription ───────────────────────────────────────────────────

    async def upgrade_to_premium(
        self,
        user_id: int,
        expires_at: datetime,
        tx_hash: str,
    ) -> None:
        """Set a user's tier to "premium" with an expiry datetime.

        Args:
            user_id: Telegram numeric user ID.
            expires_at: UTC datetime when premium expires.
            tx_hash: on-chain transaction hash for audit log.
        """
        async with self._session_factory() as session:
            await session.execute(
                update(User)
                .where(User.telegram_id == user_id)
                .values(tier="premium", premium_expires_at=expires_at)
            )
            await session.commit()
        logger.info(
            "[DB] User %d upgraded to premium until %s | tx: %s",
            user_id,
            expires_at.strftime("%Y-%m-%d %H:%M UTC"),
            tx_hash,
        )

    async def downgrade_expired_users(self) -> int:
        """Reset tier to "free" for all users whose premium has expired.

        Returns:
            Number of users downgraded.
        """
        now = datetime.now(tz=timezone.utc)
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(
                    User.tier == "premium",
                    User.premium_expires_at < now,
                )
            )
            expired = result.scalars().all()
            count = len(expired)
            for user in expired:
                user.tier = "free"
                user.premium_expires_at = None
            await session.commit()

        if count:
            logger.info("[DB] Downgraded %d expired premium users", count)
        return count

    async def is_premium(self, user_id: int) -> bool:
        """Check whether a user currently has an active premium subscription.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            True if tier is "premium" and expiry is in the future.
        """
        now = datetime.now(tz=timezone.utc)
        async with self._session_factory() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user_id)
            )
            user = result.scalar_one_or_none()

        if user is None:
            return False
        if user.tier != "premium":
            return False
        if user.premium_expires_at is None:
            return False
        return user.premium_expires_at > now


# Global instance
db = DatabaseManager()
