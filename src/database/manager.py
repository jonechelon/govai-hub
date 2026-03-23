"""--- NEON MIGRATION: run manually before first production deploy ---

BEGIN;

CREATE TABLE IF NOT EXISTS governance_alerts (
    proposal_id     BIGINT PRIMARY KEY,
    proposer        VARCHAR(42) NOT NULL,
    description_url VARCHAR(500),
    deposit_cusd    NUMERIC(36, 18),
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

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.user_network import effective_user_network

from src.database.models import (
    APPS_AVAILABLE,
    AsyncSessionLocal,
    engine,
    DigestLog,
    FetcherLog,
    GovernanceVote,
    GovernanceAlert,
    SystemState,
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

    # ─── set_preferred_network / set_chain_network ───────────────────────────

    async def set_chain_network(self, user_id: int, network: str) -> None:
        """Set both ``network`` and ``preferred_network`` to the same canonical value.

        Args:
            user_id: Telegram user id.
            network: ``mainnet`` | ``alfajores`` | ``sepolia``.
        """
        valid = {"mainnet", "alfajores", "sepolia"}
        normalized = network.strip().lower()
        if normalized not in valid:
            raise ValueError(
                f"Invalid chain network: {network!r}. Must be one of: {valid}"
            )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(network=normalized, preferred_network=normalized)
                )
        logger.info(
            "[DB] chain network updated | user=%s | network=%s",
            user_id,
            normalized,
        )

    async def set_preferred_network(self, user_id: int, preferred_network: str) -> None:
        """Set user's governance network preference (mainnet | alfajores).

        Updates both ORM columns via :meth:`set_chain_network` for consistency.
        """
        normalized = preferred_network.strip().lower()
        if normalized not in {"mainnet", "alfajores"}:
            raise ValueError(f"Invalid preferred_network: {preferred_network!r}")
        await self.set_chain_network(user_id, normalized)

    # ─── toggle_notifications_enabled ────────────────────────────────────────

    async def toggle_notifications_enabled(self, user_id: int) -> bool:
        """Invert notifications_enabled for the user and return the new value.

        Returns:
            The new boolean value of notifications_enabled.

        Raises:
            ValueError: if the user does not exist.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = result.scalar_one_or_none()
                if user is None:
                    raise ValueError(f"User not found: {user_id}")
                user.notifications_enabled = not bool(user.notifications_enabled)
                new_value = bool(user.notifications_enabled)

        logger.info(
            "[DB] notifications_enabled toggled | user=%s | enabled=%s",
            user_id,
            new_value,
        )
        return new_value

    # ─── get_all_subscribers_with_notifications_enabled ──────────────────────

    async def get_all_subscribers_with_notifications_enabled(self) -> list[int]:
        """Return user_ids for subscribed users with notifications_enabled=True."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User.user_id).where(
                        User.subscribed == True,  # noqa: E712
                        User.notifications_enabled == True,  # noqa: E712
                    )
                )
                return list(result.scalars().all())

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

    # ─── set_user_wallet ─────────────────────────────────────────────────────

    async def set_user_wallet(self, user_id: int, wallet_address: str) -> None:
        """Save or update the user's governance wallet address (LockedGold user wallet)."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.user_id == user_id))
                user = result.scalar_one_or_none()
                if user:
                    user.user_wallet = wallet_address
        logger.info(
            "[DB] Governance wallet set | user=%s | user_wallet=%s", user_id, wallet_address
        )

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
                current_delegated = bool(user.delegated_power)
                user.delegated_power = delegated

                if delegated:
                    # Clear any previous revocation timestamp when delegation is present.
                    user.revoked_at = None
                else:
                    # Set revoked_at only on transition from delegated=True → delegated=False.
                    # This prevents repeatedly overwriting the revocation time on every /govstatus call.
                    if current_delegated:
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
                deposit_cusd=proposal.get("deposit"),
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

    async def list_user_governance_votes(
        self, user_id: int, limit: int = 40
    ) -> list[GovernanceVote]:
        """Return the user's governance vote intents, newest first."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(GovernanceVote)
                    .where(GovernanceVote.user_id == user_id)
                    .order_by(GovernanceVote.created_at.desc())
                    .limit(limit)
                )
                return list(result.scalars().all())

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
            # Choose ABSTAIN when no strict majority exists (ties across the top counts).
            max_count = max(counts.values())
            top_choices = [choice for choice, c in counts.items() if c == max_count]
            majority_choice = top_choices[0] if len(top_choices) == 1 else "ABSTAIN"
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

    # ─── system state (persistent key-value) ─────────────────────────────────

    async def get_system_state(self, key: str) -> str | None:
        """Retrieve a key-value pair from the system_state table.

        Args:
            key: State key to look up (e.g. "governance_last_block").

        Returns:
            The stored string value, or None if the key does not exist.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(SystemState).where(SystemState.key == key)
                )
                row = result.scalar_one_or_none()
                return row.value if row else None

    async def set_system_state(self, key: str, value: str) -> None:
        """Insert or update a key-value pair in the system_state table.

        Args:
            key: State key (max 50 chars).
            value: State value to persist (max 255 chars).
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(SystemState).where(SystemState.key == key)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.value = value
                else:
                    session.add(SystemState(key=key, value=value))

    async def save_ai_session(
        self,
        user_id: int,
        session_id: str,
        suggestions: list[dict],
    ) -> None:
        """Persist AI trade suggestions to ai_trade_sessions table.

        suggestions is serialized to JSON text for storage.

        Args:
            user_id: Telegram user id.
            session_id: Short unique session id (e.g. 8-char prefix).
            suggestions: Parsed suggestion dicts from parse_ai_suggestions.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO ai_trade_sessions (user_id, session_id, suggestions_json) "
                        "VALUES (:user_id, :session_id, :suggestions_json) "
                        "ON CONFLICT (session_id) DO NOTHING"
                    ),
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "suggestions_json": json.dumps(suggestions),
                    },
                )

    async def get_ai_session(self, session_id: str) -> list[dict] | None:
        """Retrieve AI suggestions for a session_id if created within the last 24 hours.

        Returns:
            Parsed suggestions list, or None if not found or TTL expired.
        """
        backend = engine.url.get_backend_name()
        if backend == "postgresql":
            ttl_clause = "created_at > NOW() - INTERVAL '24 hours'"
        else:
            # SQLite (local dev): compare stored timestamps
            ttl_clause = "datetime(created_at) > datetime('now', '-24 hours')"

        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT suggestions_json FROM ai_trade_sessions "
                        "WHERE session_id = :session_id "
                        f"AND {ttl_clause}"
                    ),
                    {"session_id": session_id},
                )
                row = result.fetchone()
                if not row:
                    return None
                return json.loads(row[0])

    async def save_auto_trade(
        self,
        user_id: int,
        proposal_id: int,
        intent_json: dict,
    ) -> int:
        """Persist a pending auto-trade intent linked to a governance proposal.

        Args:
            user_id: Telegram user id.
            proposal_id: On-chain governance proposal id.
            intent_json: Intent payload (serialized to JSON).

        Returns:
            The new row id.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "INSERT INTO auto_trades (user_id, proposal_id, intent_json) "
                        "VALUES (:user_id, :proposal_id, :intent_json) "
                        "RETURNING id"
                    ),
                    {
                        "user_id": user_id,
                        "proposal_id": proposal_id,
                        "intent_json": json.dumps(intent_json),
                    },
                )
                row = result.fetchone()
                if row is None:
                    raise RuntimeError("auto_trades insert returned no id")
                return int(row[0])

    async def get_proposal_ids_with_pending_auto_trades(self) -> list[int]:
        """Return distinct proposal IDs that have pending auto-trade notifications."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT DISTINCT proposal_id FROM auto_trades "
                    "WHERE executed = false AND notified = 0"
                ),
            )
            return [int(r[0]) for r in result.fetchall()]

    async def get_pending_auto_trades(self, proposal_id: int) -> list[dict]:
        """
        Returns all auto_trades with executed=false and notified=0
        for the given proposal_id.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT id, user_id, intent_json FROM auto_trades "
                    "WHERE proposal_id = :proposal_id "
                    "AND executed = false "
                    "AND notified = 0"
                ),
                {"proposal_id": proposal_id},
            )
            rows = result.mappings().all()
            return [dict(row) for row in rows]

    async def mark_auto_trade_notified(self, trade_id: int) -> None:
        """
        Sets notified=1 for a given auto_trade row.
        Called after successfully sending (or attempting) the Telegram notification.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("UPDATE auto_trades SET notified = 1 WHERE id = :trade_id"),
                    {"trade_id": trade_id},
                )

    async def get_user_pending_trades(self, user_id: int) -> list[dict]:
        """
        Returns all pending auto_trades for a specific user.

        Pending = executed=false AND notified=0.
        Used by gov:status to show the user their active intents.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT id, proposal_id, intent_json, created_at "
                    "FROM auto_trades "
                    "WHERE user_id = :user_id "
                    "AND executed = false "
                    "AND notified = 0 "
                    "ORDER BY created_at DESC"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
            return [
                {
                    "id": int(row["id"]),
                    "proposal_id": int(row["proposal_id"]),
                    "intent_json": row["intent_json"],
                    "created_at": str(row["created_at"])
                    if row["created_at"] is not None
                    else "",
                }
                for row in rows
            ]

    async def cancel_auto_trade(self, trade_id: int, user_id: int) -> bool:
        """
        Cancels an auto_trade by setting executed=true.

        user_id is required for ownership verification — never cancel another user's trade.

        Returns:
            True if a row was actually updated, False if not found or wrong user.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "UPDATE auto_trades SET executed = true "
                        "WHERE id = :trade_id AND user_id = :user_id"
                    ),
                    {"trade_id": trade_id, "user_id": user_id},
                )
                return result.rowcount > 0

    async def get_user_trade_for_proposal(
        self,
        user_id: int,
        proposal_id: int,
    ) -> dict | None:
        """Return the most recent active auto_trade for a (user_id, proposal_id) pair.

        Used to prevent duplicate registrations for the same proposal.
        Active = executed=FALSE (includes notified and un-notified).
        Returns None if no active trade exists.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT id, intent_json, created_at FROM auto_trades "
                    "WHERE user_id = :user_id "
                    "AND proposal_id = :proposal_id "
                    "AND executed = false "
                    "ORDER BY created_at DESC "
                    "LIMIT 1"
                ),
                {"user_id": user_id, "proposal_id": proposal_id},
            )
            row = result.fetchone()
            if not row:
                return None
            raw_intent = row[1]
            if isinstance(raw_intent, str):
                try:
                    parsed_intent: dict | str = json.loads(raw_intent)
                except json.JSONDecodeError:
                    parsed_intent = raw_intent
            else:
                parsed_intent = raw_intent
            created = row[2]
            return {
                "id": int(row[0]),
                "intent_json": parsed_intent,
                "created_at": str(created) if created is not None else "",
            }

    async def get_user_wallet_by_username(self, username: str) -> str | None:
        """Return governance wallet (user_wallet) for a Telegram username, or None.

        Matches usernames stored with or without a leading ``@`` (case-insensitive).
        """
        raw = username.lstrip("@")
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User.user_wallet).where(
                        func.lower(func.replace(User.username, "@", "")) == raw.lower(),
                        User.user_wallet.isnot(None),
                    ).limit(1)
                )
                return result.scalar_one_or_none()

    async def save_payout_request(
        self,
        chat_id: int,
        requester_id: int,
        recipient_username: str,
        recipient_wallet: str,
        amount: str,
        token: str = "CELO",
    ) -> int:
        """Insert a payout_requests row; ``expires_at`` uses the DB default (NOW + 24h).

        Returns:
            New row ``id``.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "INSERT INTO payout_requests "
                        "(chat_id, requester_id, recipient_username, recipient_wallet, amount, token) "
                        "VALUES (:chat_id, :requester_id, :recipient_username, :recipient_wallet, "
                        ":amount, :token) "
                        "RETURNING id"
                    ),
                    {
                        "chat_id": chat_id,
                        "requester_id": requester_id,
                        "recipient_username": recipient_username,
                        "recipient_wallet": recipient_wallet,
                        "amount": amount,
                        "token": token,
                    },
                )
                row = result.fetchone()
                if row is None:
                    raise RuntimeError("payout_requests insert returned no id")
                return int(row[0])

    async def update_payout_message_id(
        self, payout_id: int, message_id: int
    ) -> None:
        """Set ``message_id`` on a payout request after the receipt is sent."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE payout_requests SET message_id = :message_id WHERE id = :payout_id"
                    ),
                    {"message_id": message_id, "payout_id": payout_id},
                )

    async def get_payout_request(self, payout_id: int) -> dict | None:
        """Load one ``payout_requests`` row by id, or None if missing."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT id, chat_id, message_id, requester_id, recipient_username, "
                    "recipient_wallet, amount, token, approvals_json, status, "
                    "created_at, expires_at FROM payout_requests WHERE id = :id"
                ),
                {"id": payout_id},
            )
            row = result.fetchone()
            if row is None:
                return None
            return {
                "id": int(row[0]),
                "chat_id": int(row[1]),
                "message_id": row[2],
                "requester_id": int(row[3]),
                "recipient_username": row[4] or "",
                "recipient_wallet": row[5] or "",
                "amount": row[6] or "",
                "token": row[7] or "",
                "approvals_json": row[8] if row[8] is not None else "[]",
                "status": row[9] or "",
                "created_at": row[10],
                "expires_at": row[11],
            }

    async def add_payout_approval(self, payout_id: int, user_id: int) -> bool:
        """Append ``user_id`` to ``approvals_json`` if pending and not already present.

        Returns:
            True if a new approval was stored, False if duplicate or not pending.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT approvals_json, status FROM payout_requests WHERE id = :id"
                    ),
                    {"id": payout_id},
                )
                row = result.fetchone()
                if row is None:
                    return False
                approvals_json, status = row[0], row[1]
                if (status or "") != "pending":
                    return False
                try:
                    approvals: list = json.loads(approvals_json or "[]")
                except json.JSONDecodeError:
                    approvals = []
                if not isinstance(approvals, list):
                    approvals = []
                if user_id in approvals:
                    return False
                approvals.append(user_id)
                await session.execute(
                    text(
                        "UPDATE payout_requests SET approvals_json = :aj "
                        "WHERE id = :id AND status = 'pending'"
                    ),
                    {"aj": json.dumps(approvals), "id": payout_id},
                )
            return True

    async def set_payout_status(self, payout_id: int, status: str) -> None:
        """Set ``status`` on a payout request (e.g. ``expired``, ``approved``)."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("UPDATE payout_requests SET status = :st WHERE id = :id"),
                    {"st": status, "id": payout_id},
                )

    # Phase 7 — set the preferred Celo network for a user

    async def set_user_network(self, user_id: int, network: str) -> None:
        """Persist network preference (Phase 7 API). Delegates to :meth:`set_chain_network`."""
        await self.set_chain_network(user_id, network)

    # Phase 7 — retrieve the preferred Celo network for a user (default: 'mainnet')

    async def get_user_network(self, user_id: int) -> str:
        """Return the user's effective chain network (reconciles ``network`` and legacy rows)."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = result.scalar_one_or_none()
        if not user:
            return "mainnet"
        return effective_user_network(user)

    # ─── Referral economy (P-ECO.3) ───────────────────────────────────────────

    async def set_referred_by(self, user_id: int, referrer_id: int) -> None:
        """Write referred_by only if not already set (immutable after first write)."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE users
                           SET referred_by = :referrer_id
                         WHERE user_id = :user_id
                           AND referred_by IS NULL
                        """
                    ),
                    {"referrer_id": referrer_id, "user_id": user_id},
                )

    async def add_gov_points(self, user_id: int, points: int) -> None:
        """Atomically increment gov_points for a user."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE users SET gov_points = COALESCE(gov_points, 0) + :points "
                        "WHERE user_id = :user_id"
                    ),
                    {"points": points, "user_id": user_id},
                )

    async def create_referral(
        self, referrer_id: int, referee_id: int, proposal_id: int
    ) -> None:
        """Insert referral row; silently ignore if referee already has a referral."""
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO referrals (referrer_id, referee_id, proposal_id)
                        VALUES (:referrer_id, :referee_id, :proposal_id)
                        ON CONFLICT (referee_id) DO NOTHING
                        """
                    ),
                    {
                        "referrer_id": referrer_id,
                        "referee_id": referee_id,
                        "proposal_id": proposal_id,
                    },
                )

    async def get_referral_stats(self, user_id: int) -> dict:
        """
        Return referral stats for a user.
        Keys: referral_count (int), total_swap_count (int),
              total_earned_usdm (str), gov_points (int).
        All keys always present; defaults to zero values on empty result.
        """
        backend = engine.url.get_backend_name()
        if backend == "postgresql":
            sql_referrals = """
                SELECT COUNT(*) AS referral_count,
                       COALESCE(SUM(swap_count), 0) AS total_swap_count,
                       COALESCE(SUM(earned_usdm::numeric), 0) AS total_earned_usdm
                  FROM referrals
                 WHERE referrer_id = :user_id
            """
        else:
            sql_referrals = """
                SELECT COUNT(*) AS referral_count,
                       COALESCE(SUM(swap_count), 0) AS total_swap_count,
                       COALESCE(SUM(CAST(earned_usdm AS REAL)), 0) AS total_earned_usdm
                  FROM referrals
                 WHERE referrer_id = :user_id
            """
        sql_points = (
            "SELECT COALESCE(gov_points, 0) AS gov_points FROM users "
            "WHERE user_id = :user_id"
        )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(sql_referrals),
                    {"user_id": user_id},
                )
                row = result.mappings().first()
                result_pts = await session.execute(
                    text(sql_points),
                    {"user_id": user_id},
                )
                points_row = result_pts.mappings().first()

        return {
            "referral_count": int(row["referral_count"]) if row else 0,
            "total_swap_count": int(row["total_swap_count"]) if row else 0,
            "total_earned_usdm": str(row["total_earned_usdm"]) if row else "0",
            "gov_points": int(points_row["gov_points"]) if points_row else 0,
        }


db = DatabaseManager()
