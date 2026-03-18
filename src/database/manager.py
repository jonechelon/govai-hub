"""--- NEON MIGRATION: run manually before first production deploy ---

BEGIN;

CREATE TABLE IF NOT EXISTS governance_alerts (
    proposal_id     BIGINT PRIMARY KEY,
    proposer        VARCHAR(42) NOT NULL,
    description_url VARCHAR(500),
    deposit_celo    FLOAT,
    queued_at       TIMESTAMPTZ NOT NULL,
    block_number    BIGINT NOT NULL,
    tx_hash         VARCHAR(66) NOT NULL UNIQUE,
    sent_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_governance_alerts_sent_at
    ON governance_alerts(sent_at);

CREATE INDEX IF NOT EXISTS ix_governance_alerts_queued_at
    ON governance_alerts(queued_at);

COMMIT;
"""

# src/database/manager.py
# Celo GovAI Hub — DatabaseManager singleton (async, SQLAlchemy + asyncpg / Neon)

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    APPS_AVAILABLE,
    AsyncSessionLocal,
    DigestLog,
    FetcherLog,
    GovernanceVote,
    GovernanceAlert,
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

    # ─── get_user ───────────────────────────────────────────────────────────

    async def get_user(self, user_id: int) -> User | None:
        """Fetch a single user by user_id. Returns None if not found."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                return result.scalar_one_or_none()

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

    # ─── count_subscribers ───────────────────────────────────────────────────

    async def count_subscribers(self) -> int:
        """Count users with subscribed=True."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.count()).select_from(User).where(
                        User.subscribed == True  # noqa: E712
                    )
                )
                return result.scalar_one() or 0

    # ─── count_premium_users ─────────────────────────────────────────────────

    async def count_premium_users(self) -> int:
        """Count users with active premium (tier=premium and premium_expires_at > now)."""
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.count()).select_from(User).where(
                        User.tier == "premium",
                        User.premium_expires_at > now,
                    )
                )
                return result.scalar_one() or 0

    # ─── get_last_digest_at ──────────────────────────────────────────────────

    async def get_last_digest_at(self) -> datetime | None:
        """Return the most recent DigestLog.generated_at timestamp."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(DigestLog.generated_at)
                    .order_by(DigestLog.generated_at.desc())
                    .limit(1)
                )
                return result.scalar_one_or_none()

    # ─── get_last_fetch_at ───────────────────────────────────────────────────

    async def get_last_fetch_at(self) -> datetime | None:
        """Return the most recent FetcherLog.fetched_at timestamp."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(FetcherLog.fetched_at)
                    .order_by(FetcherLog.fetched_at.desc())
                    .limit(1)
                )
                return result.scalar_one_or_none()

    # ─── count_digests_today ─────────────────────────────────────────────────

    async def count_digests_today(self) -> int:
        """Count DigestLog entries created today (UTC)."""
        today = datetime.now(timezone.utc).date()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.count(DigestLog.id)).where(
                        func.date(DigestLog.generated_at) == today
                    )
                )
                return result.scalar_one() or 0

    # ─── sum_groq_tokens_today ───────────────────────────────────────────────

    async def sum_groq_tokens_today(self) -> int:
        """Sum of Groq tokens used in DigestLog today (UTC)."""
        today = datetime.now(timezone.utc).date()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.sum(DigestLog.groq_tokens)).where(
                        func.date(DigestLog.generated_at) == today
                    )
                )
                return int(result.scalar_one() or 0)

    # ─── count_errors_today ──────────────────────────────────────────────────

    async def count_errors_today(self) -> int:
        """Count DigestLog entries with errors > 0 today (UTC)."""
        today = datetime.now(timezone.utc).date()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(func.sum(DigestLog.errors)).where(
                        func.date(DigestLog.generated_at) == today
                    )
                )
                return int(result.scalar_one() or 0)

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
        """Set tier=premium, premium_expires_at, and premium_tx_hash for replay protection."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(
                        tier="premium",
                        premium_expires_at=expires_at,
                        premium_tx_hash=tx_hash,
                    )
                )
        logger.info(
            "[DB] User %s upgraded to premium until %s | tx: %s",
            user_id,
            expires_at,
            tx_hash,
        )

    # ─── is_tx_hash_used ─────────────────────────────────────────────────────

    async def is_tx_hash_used(self, tx_hash: str) -> bool:
        """Return True if tx_hash was already used to activate Premium (replay protection)."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User).where(User.premium_tx_hash == tx_hash)
                )
                return result.scalar_one_or_none() is not None

    # ─── set_premium ─────────────────────────────────────────────────────────

    async def set_premium(
        self,
        user_id: int,
        expires_at: datetime,
        tx_hash: str,
    ) -> None:
        """Activate premium for user — stores tx_hash to prevent replay attacks."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    user.tier = "premium"
                    user.premium_expires_at = expires_at
                    user.premium_tx_hash = tx_hash
        logger.info("[DB] Premium set | user=%s | expires=%s | tx=%s", user_id, expires_at, tx_hash)

    # ─── set_wallet ───────────────────────────────────────────────────────────

    async def set_wallet(self, user_id: int, wallet_address: str) -> None:
        """Save or update the user's personal Celo wallet address."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    user.wallet_address = wallet_address
        logger.info("[DB] Wallet set | user=%s | wallet=%s", user_id, wallet_address)

    # ─── get_wallet ───────────────────────────────────────────────────────────

    async def get_wallet(self, user_id: int) -> str | None:
        """Return the registered wallet address for a user, or None."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                return user.wallet_address if user else None

    # ─── get_user_by_wallet ───────────────────────────────────────────────────

    async def get_user_by_wallet(self, wallet_address: str) -> int | None:
        """Look up user_id by registered wallet address (case-insensitive).

        Returns user_id if found, None otherwise.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User).where(
                        func.lower(User.wallet_address) == wallet_address.lower()
                    )
                )
                user = result.scalar_one_or_none()
                return user.user_id if user else None

    # ─── delegation helpers ────────────────────────────────────────────────────

    async def set_delegation_status(self, user_id: int, delegated: bool) -> None:
        """Update delegated_power and revoked_at for a user in a single transaction."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                if not user:
                    logger.warning("[DB] User %s not found — cannot update delegation status", user_id)
                    return
                user.delegated_power = delegated
                if delegated:
                    user.revoked_at = None
                else:
                    user.revoked_at = datetime.now(timezone.utc)
        logger.info("[DB] Delegation status updated | user=%s | delegated=%s", user_id, delegated)

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

    # ─── governance alerts ────────────────────────────────────────────────────

    async def log_governance_alert(self, proposal: dict) -> None:
        """Insert a governance proposal alert, ignoring duplicates by proposal_id."""
        async with AsyncSessionLocal() as session:
            alert = GovernanceAlert(
                proposal_id=proposal["proposal_id"],
                proposer=proposal["proposer"],
                description_url=proposal.get("description_url"),
                deposit_celo=proposal.get("deposit"),
                queued_at=proposal["queued_at"],
                block_number=proposal["block_number"],
                tx_hash=proposal["tx_hash"],
                sent_at=None,
            )
            try:
                session.add(alert)
                await session.commit()
                logger.info(
                    "[DB] Governance alert logged | proposal_id: %s | proposer: %s",
                    proposal["proposal_id"],
                    proposal["proposer"],
                )
            except Exception:
                # Duplicate proposal_id or tx_hash — skip silently (idempotent)
                await session.rollback()

    async def get_unsent_alerts(self) -> list[GovernanceAlert]:
        """Return all governance alerts not yet broadcast, ordered by queued_at ASC."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceAlert)
                    .where(GovernanceAlert.sent_at.is_(None))
                    .order_by(GovernanceAlert.queued_at.asc())
                )
                return list(result.scalars().all())

    async def mark_alert_sent(self, proposal_id: int) -> None:
        """Mark a governance alert as sent by setting sent_at to current UTC time."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceAlert).where(
                        GovernanceAlert.proposal_id == proposal_id
                    )
                )
                alert = result.scalar_one_or_none()
                if alert:
                    alert.sent_at = datetime.now(timezone.utc)
                    await session.commit()

    async def get_recent_alerts(self, limit: int = 5) -> list[GovernanceAlert]:
        """Return the N most recent governance alerts for the /governance command."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceAlert)
                    .order_by(GovernanceAlert.queued_at.desc())
                    .limit(limit)
                )
                return list(result.scalars().all())

    async def get_alert_by_id(self, proposal_id: int) -> GovernanceAlert | None:
        """Return a single governance alert by proposal_id, or None if not found."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceAlert).where(
                        GovernanceAlert.proposal_id == proposal_id
                    )
                )
                return result.scalar_one_or_none()

    # ─── governance votes ───────────────────────────────────────────────────────

    async def register_vote_intent(
        self,
        user_id: int,
        proposal_id: int,
        vote_choice: str,
    ) -> None:
        """Upsert a governance vote intent for a user and proposal.

        If a row already exists for (user_id, proposal_id), the vote_choice is updated.
        """
        normalized_choice = vote_choice.upper()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceVote).where(
                        GovernanceVote.user_id == user_id,
                        GovernanceVote.proposal_id == proposal_id,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is None:
                    session.add(
                        GovernanceVote(
                            user_id=user_id,
                            proposal_id=proposal_id,
                            vote_choice=normalized_choice,
                        )
                    )
                else:
                    existing.vote_choice = normalized_choice


    async def get_pending_votes_aggregated(self) -> list[dict]:
        """Aggregate pending governance votes by proposal and compute majority choice.

        Pending votes are those where executed_tx_hash IS NULL. For each proposal_id, this
        method returns a dict with:
            - proposal_id: int
            - majority_choice: str ("YES", "NO", or "ABSTAIN")
            - user_ids: list[int] of users who have an intent registered
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(
                        GovernanceVote.proposal_id,
                        GovernanceVote.user_id,
                        GovernanceVote.vote_choice,
                    ).where(GovernanceVote.executed_tx_hash.is_(None))
                )
                rows = result.all()

        aggregated: dict[int, dict] = {}
        for proposal_id, user_id, vote_choice in rows:
            bucket = aggregated.setdefault(
                proposal_id,
                {
                    "proposal_id": proposal_id,
                    "counts": {"YES": 0, "NO": 0, "ABSTAIN": 0},
                    "user_ids": [],
                },
            )
            normalized = (vote_choice or "").upper()
            if normalized not in {"YES", "NO", "ABSTAIN"}:
                logger.warning(
                    "[DB] Ignoring invalid vote_choice '%s' for proposal %s",
                    vote_choice,
                    proposal_id,
                )
                continue
            bucket["counts"][normalized] += 1
            bucket["user_ids"].append(user_id)

        result_list: list[dict] = []
        for proposal_id, data in aggregated.items():
            counts = data["counts"]
            # Deterministic tie-breaking order: YES > NO > ABSTAIN
            majority_choice = max(
                ["YES", "NO", "ABSTAIN"],
                key=lambda choice: counts.get(choice, 0),
            )
            result_list.append(
                {
                    "proposal_id": proposal_id,
                    "majority_choice": majority_choice,
                    "user_ids": data["user_ids"],
                }
            )

        logger.info(
            "[DB] Aggregated pending governance votes | proposals=%s",
            len(result_list),
        )
        return result_list

    async def mark_votes_executed(self, proposal_id: int, tx_hash: str) -> None:
        """Mark all pending votes for a proposal as executed with the given tx hash."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(GovernanceVote)
                    .where(
                        and_(
                            GovernanceVote.proposal_id == proposal_id,
                            GovernanceVote.executed_tx_hash.is_(None),
                        )
                    )
                    .values(executed_tx_hash=tx_hash)
                )
        logger.info(
            "[DB] Governance votes marked executed | proposal_id=%s | tx=%s",
            proposal_id,
            tx_hash,
        )


db = DatabaseManager()
