# src/database/manager.py
# Up-to-Celo — DatabaseManager singleton (async, SQLAlchemy + aiosqlite)

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    APPS_AVAILABLE,
    AsyncSessionLocal,
    DigestLog,
    User,
    UserAppFilter,
    init_db as models_init_db,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async database manager — singleton. All access via `from src.database.manager import db`."""

    _instance: DatabaseManager | None = None

    def __new__(cls) -> DatabaseManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ─── init_db ─────────────────────────────────────────────────────────────

    async def init_db(self) -> None:
        """Create all tables if they do not exist. Safe to call on every startup."""
        await models_init_db()
        logger.info("[DB] Database initialized")

    # ─── get_or_create_user ─────────────────────────────────────────────────

    async def get_or_create_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
    ) -> User:
        """Return existing user or create one; always update username and first_name."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()

                if user is None:
                    user = User(
                        user_id=user_id,
                        username=username,
                        first_name=first_name,
                        subscribed=True,
                        tier="free",
                    )
                    session.add(user)
                    await session.flush()
                    await self._init_user_apps(session, user_id)
                else:
                    user.username = username
                    user.first_name = first_name

                await session.refresh(user)
                return user

    async def _init_user_apps(self, session: AsyncSession, user_id: int) -> None:
        """Create UserAppFilter rows for every app in APPS_AVAILABLE (enabled=True)."""
        for category, apps in APPS_AVAILABLE.items():
            for app_name in apps:
                session.add(
                    UserAppFilter(
                        user_id=user_id,
                        app_name=app_name,
                        category=category,
                        enabled=True,
                    )
                )

    # ─── update_subscription ────────────────────────────────────────────────

    async def update_subscription(self, user_id: int, subscribed: bool) -> None:
        """Update user's subscribed flag. No-op with warning if user does not exist."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                if user is None:
                    logger.warning("[DB] User %s not found — cannot update subscription", user_id)
                    return
                user.subscribed = subscribed
        logger.info("[DB] User %s subscription updated: %s", user_id, subscribed)

    # ─── get_all_subscribers ─────────────────────────────────────────────────

    async def get_all_subscribers(self) -> list[int]:
        """Return list of user_id for all users with subscribed=True."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User.user_id).where(User.subscribed == True)  # noqa: E712
                )
                return list(result.scalars().all())

    # ─── get_user_apps ───────────────────────────────────────────────────────

    async def get_user_apps(self, user_id: int) -> list[str]:
        """Return list of app_name (lowercase) with enabled=True. Fallback: all APPS_AVAILABLE."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserAppFilter.app_name).where(
                        UserAppFilter.user_id == user_id,
                        UserAppFilter.enabled == True,  # noqa: E712
                    )
                )
                names = list(result.scalars().all())

        if not names:
            return [app for apps in APPS_AVAILABLE.values() for app in apps]
        return names

    # ─── get_user_apps_by_category ───────────────────────────────────────────

    async def get_user_apps_by_category(self, user_id: int) -> dict[str, list[str]]:
        """Return {category: [app_name, ...]} for enabled apps only. Exclude empty categories."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserAppFilter.category, UserAppFilter.app_name).where(
                        UserAppFilter.user_id == user_id,
                        UserAppFilter.enabled == True,  # noqa: E712
                    )
                )
                rows = result.all()

        by_cat: dict[str, list[str]] = {}
        for category, app_name in rows:
            by_cat.setdefault(category, []).append(app_name)

        # Exclude categories with no enabled apps
        out = {k: v for k, v in by_cat.items() if v}
        if not out:
            return dict(APPS_AVAILABLE)
        return out

    # ─── update_user_app ─────────────────────────────────────────────────────

    async def update_user_app(
        self,
        user_id: int,
        app_name: str,
        enabled: bool,
    ) -> None:
        """Set enabled for (user_id, app_name). Upsert if row does not exist. app_name is lowercase."""
        category = ""
        for cat, apps in APPS_AVAILABLE.items():
            if app_name in apps:
                category = cat
                break

        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserAppFilter).where(
                        UserAppFilter.user_id == user_id,
                        UserAppFilter.app_name == app_name,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    session.add(
                        UserAppFilter(
                            user_id=user_id,
                            app_name=app_name,
                            category=category,
                            enabled=enabled,
                        )
                    )
                else:
                    row.enabled = enabled

    # ─── count_enabled_apps ──────────────────────────────────────────────────

    async def count_enabled_apps(self, user_id: int) -> int:
        """Return count of UserAppFilter with enabled=True for the user."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserAppFilter).where(
                        UserAppFilter.user_id == user_id,
                        UserAppFilter.enabled == True,  # noqa: E712
                    )
                )
                return len(result.scalars().all())

    # ─── log_digest ───────────────────────────────────────────────────────────

    async def log_digest(
        self,
        recipients: int,
        groq_tokens: int,
        items: int,
        errors: int,
    ) -> None:
        """Persist one DigestLog row. On failure log warning and continue."""
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(
                        DigestLog(
                            recipients_count=recipients,
                            groq_tokens=groq_tokens,
                            items_fetched=items,
                            errors=errors,
                        )
                    )
        except Exception as exc:
            logger.warning("[DB] Failed to log digest | error: %s", exc)

    # ─── upgrade_to_premium ───────────────────────────────────────────────────

    async def upgrade_to_premium(
        self,
        user_id: int,
        expires_at: datetime,
        tx_hash: str,
    ) -> None:
        """Set tier=premium and premium_expires_at. tx_hash is for log only."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(tier="premium", premium_expires_at=expires_at)
                )
        logger.info(
            "[DB] User %s upgraded to premium until %s | tx: %s",
            user_id,
            expires_at,
            tx_hash,
        )

    # ─── downgrade_expired_users ──────────────────────────────────────────────

    async def downgrade_expired_users(self) -> int:
        """Set tier=free and premium_expires_at=None for expired premium users. Return count."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
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

        if count:
            logger.info("[DB] Downgraded %s expired premium users", count)
        return count

    # ─── is_premium ───────────────────────────────────────────────────────────

    async def is_premium(self, user_id: int) -> bool:
        """Return True if tier is premium and premium_expires_at > now; else False."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()

        if user is None:
            return False
        if user.tier != "premium":
            return False
        if user.premium_expires_at is None:
            return False
        return user.premium_expires_at > now


db = DatabaseManager()
