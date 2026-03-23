# src/database/models.py
# Celo GovAI Hub — SQLAlchemy async models.
# Production: PostgreSQL via Neon (asyncpg driver).
# Development: SQLite fallback (set DATABASE_URL in .env).

from __future__ import annotations

import os
import re
import ssl
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


_raw_url = os.getenv("DATABASE_URL", "")

if _raw_url:
    # Drop sslmode / channel_binding — asyncpg uses connect_args ssl= instead.
    # Regex-only stripping can leave "&channel_binding=..." glued to the DB path.
    _parsed = urlparse(_raw_url)
    _filtered_query = [
        (k, v)
        for k, v in parse_qsl(_parsed.query, keep_blank_values=True)
        if k not in ("sslmode", "channel_binding")
    ]
    _clean_url = urlunparse(
        (
            _parsed.scheme,
            _parsed.netloc,
            _parsed.path,
            _parsed.params,
            urlencode(_filtered_query),
            _parsed.fragment,
        )
    )
    # Force asyncpg driver
    _clean_url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", _clean_url)

    # SSL context for Neon PostgreSQL
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(
        _clean_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=240,
        connect_args={"ssl": _ssl_ctx},
    )
else:
    # Development fallback: local SQLite
    from src.utils.paths import DATA_DIR
    SQLITE_PATH = DATA_DIR / "up-to-celo.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{SQLITE_PATH}",
        echo=False,
    )

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ─── Declarative base ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Apps available (24 apps across 4 merged categories) ─────────────────────────
# Category keys must match CATEGORY_DISPLAY in src/bot/keyboards.py.

APPS_AVAILABLE: dict[str, list[str]] = {
    "payments": ["minipay", "valora", "halofi", "hurupay"],
    "defi": [
        "ubeswap",
        "moola",
        "mento",
        "symmetric",
        "mobius",
        "knox",
        "equalizer",
        "uniswap",
    ],
    "onramp_nft": [
        "celocashflow",
        "unipos",
        "octoplace",
        "hypermove",
        "truefeedback",
    ],
    "refi_social": [
        "toucan",
        "toucanrefi",
        "impactmarket",
        "masa",
        "celonetwork",
        "celoreserve",
    ],
}


# ─── Models ──────────────────────────────────────────────────────────────────

class User(Base):
    """Telegram user registered with the bot."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_digest_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    tier: Mapped[str] = mapped_column(String(16), default="free")
    # Valid values: "free" | "premium"
    premium_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    preferred_network: Mapped[str] = mapped_column(
        String(16),
        default="mainnet",
        server_default="mainnet",
        nullable=False,
    )
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
        nullable=False,
    )
    # Phase 7 — Celo chain target (mainnet / alfajores / sepolia); see also preferred_network
    network: Mapped[str] = mapped_column(
        String(32),
        default="mainnet",
        server_default="mainnet",
        nullable=False,
    )
    wallet_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True, unique=True)
    # Celo wallet address provided by the user for payment verification
    user_wallet: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    # EVM wallet address used for governance delegation via LockedGold
    delegated_power: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    premium_tx_hash: Mapped[Optional[str]] = mapped_column(
        String(66), nullable=True, unique=True
    )
    # tx hash used to activate premium — prevents replay attacks

    __table_args__ = (
        Index("ix_users_subscribed", "subscribed"),
        Index("ix_users_tier", "tier"),
        Index("ix_users_premium_expires_at", "premium_expires_at"),
    )


class UserAppFilter(Base):
    """Tracks which apps a user has enabled/disabled in their digest."""

    __tablename__ = "user_app_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    app_name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_user_app_filters_user_id", "user_id"),
        Index("ix_user_app_filters_category", "category"),
        UniqueConstraint("user_id", "app_name", name="uq_user_app"),
    )


class DigestLog(Base):
    """Log aggregated per daily broadcast (one row per run). Replaces BroadcastLog."""

    __tablename__ = "digest_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    recipients_count: Mapped[int] = mapped_column(Integer, default=0)
    groq_tokens: Mapped[int] = mapped_column(Integer, default=0)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)


class FetcherLog(Base):
    """Log per execution of each individual fetcher."""

    __tablename__ = "fetcher_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Values: "rss" | "twitter" | "market" | "onchain"
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    error_msg: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class GovernanceAlert(Base):
    """Tracks governance proposals fetched from Celo Mainnet."""

    __tablename__ = "governance_alerts"

    proposal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposer: Mapped[str] = mapped_column(String(42), nullable=False)
    description_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    deposit_cusd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=36, scale=18),
        nullable=True,
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False, unique=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # None = not yet sent; datetime = timestamp of successful broadcast

    __table_args__ = (
        Index("ix_governance_alerts_sent_at", "sent_at"),
        Index("ix_governance_alerts_queued_at", "queued_at"),
    )


class SystemState(Base):
    """Global key-value store for persistent bot state (survives deploys)."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class GovernanceVote(Base):
    """Stores governance vote intents per user and proposal."""

    __tablename__ = "governance_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    proposal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vote_choice: Mapped[str] = mapped_column(String(8), nullable=False)
    executed_tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "proposal_id", name="uq_governance_vote_user_proposal"),
        Index("ix_governance_votes_user_id", "user_id"),
        Index("ix_governance_votes_proposal_id", "proposal_id"),
    )


# ─── Schema initialization ───────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables if they do not exist. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Incremental schema migrations — run on every startup for both SQLite and PostgreSQL.
    # Each statement runs in its own transaction so that a failure (column already exists)
    # triggers a clean ROLLBACK without aborting subsequent migrations.
    # PostgreSQL raises ProgrammingError; SQLite raises OperationalError.
    _MIGRATIONS_ALTER = [
        "ALTER TABLE governance_alerts ADD COLUMN deposit_cusd FLOAT;",
        "ALTER TABLE users ADD COLUMN preferred_network VARCHAR DEFAULT 'mainnet';",
        "ALTER TABLE users ADD COLUMN notifications_enabled BOOLEAN DEFAULT true;",
    ]
    # CREATE TABLE DDL is dialect-specific (Neon/PostgreSQL vs local SQLite).
    _MIGRATIONS_CREATE_POSTGRESQL = [
        """
    CREATE TABLE IF NOT EXISTS auto_trades (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        proposal_id BIGINT NOT NULL,
        intent_json TEXT NOT NULL,
        executed BOOLEAN DEFAULT false,
        notified INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
        """
    CREATE TABLE IF NOT EXISTS ai_trade_sessions (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        session_id TEXT NOT NULL UNIQUE,
        suggestions_json TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
        """
    CREATE TABLE IF NOT EXISTS payout_requests (
        id            BIGSERIAL PRIMARY KEY,
        chat_id       BIGINT  NOT NULL,
        message_id    BIGINT,
        requester_id  BIGINT  NOT NULL,
        recipient_username TEXT NOT NULL,
        recipient_wallet   TEXT,
        amount        TEXT    NOT NULL,
        token         TEXT    NOT NULL DEFAULT 'CELO',
        approvals_json TEXT   NOT NULL DEFAULT '[]',
        status        TEXT    NOT NULL DEFAULT 'pending',
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at    TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
    )
    """,
    ]
    _MIGRATIONS_CREATE_SQLITE = [
        """
    CREATE TABLE IF NOT EXISTS auto_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id BIGINT NOT NULL,
        proposal_id BIGINT NOT NULL,
        intent_json TEXT NOT NULL,
        executed BOOLEAN DEFAULT false,
        notified INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    )
    """,
        """
    CREATE TABLE IF NOT EXISTS ai_trade_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id BIGINT NOT NULL,
        session_id TEXT NOT NULL UNIQUE,
        suggestions_json TEXT NOT NULL,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    )
    """,
        """
    CREATE TABLE IF NOT EXISTS payout_requests (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id       BIGINT  NOT NULL,
        message_id    BIGINT,
        requester_id  BIGINT  NOT NULL,
        recipient_username TEXT NOT NULL,
        recipient_wallet   TEXT,
        amount        TEXT    NOT NULL,
        token         TEXT    NOT NULL DEFAULT 'CELO',
        approvals_json TEXT   NOT NULL DEFAULT '[]',
        status        TEXT    NOT NULL DEFAULT 'pending',
        created_at    TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        expires_at    TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+24 hours'))
    )
    """,
    ]
    _backend = engine.url.get_backend_name()
    # P-ECO.2: Referral economy — referrals table (dialect-specific CREATE, shared ALTERs below)
    _eco_referrals_create_postgresql = """
    CREATE TABLE IF NOT EXISTS referrals (
        id             BIGSERIAL PRIMARY KEY,
        referrer_id    BIGINT  NOT NULL,
        referee_id     BIGINT  NOT NULL UNIQUE,
        proposal_id    BIGINT  NOT NULL,
        joined_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        first_action_at TIMESTAMPTZ,
        swap_count     INTEGER DEFAULT 0,
        earned_usdm    TEXT    DEFAULT '0'
    )
    """
    _eco_referrals_create_sqlite = """
    CREATE TABLE IF NOT EXISTS referrals (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id    BIGINT  NOT NULL,
        referee_id     BIGINT  NOT NULL UNIQUE,
        proposal_id    BIGINT  NOT NULL,
        joined_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        first_action_at TEXT,
        swap_count     INTEGER DEFAULT 0,
        earned_usdm    TEXT    DEFAULT '0'
    )
    """
    _MIGRATIONS = _MIGRATIONS_ALTER + (
        _MIGRATIONS_CREATE_POSTGRESQL
        if _backend == "postgresql"
        else _MIGRATIONS_CREATE_SQLITE
    ) + [
        # Phase 7 — add network column to users table (mainnet / alfajores / sepolia)
        "ALTER TABLE users ADD COLUMN network TEXT NOT NULL DEFAULT 'mainnet'",
        # P-ECO.2: Referral economy schema
        (
            _eco_referrals_create_postgresql
            if _backend == "postgresql"
            else _eco_referrals_create_sqlite
        ),
        "ALTER TABLE users ADD COLUMN referred_by BIGINT",
        "ALTER TABLE users ADD COLUMN gov_points INTEGER DEFAULT 0",
        # Safety: keep these ALTERs as a last-resort schema net.
        "ALTER TABLE governance_alerts ADD COLUMN deposit_cusd FLOAT;",
        "ALTER TABLE users ADD COLUMN preferred_network VARCHAR DEFAULT 'mainnet';",
        "ALTER TABLE users ADD COLUMN notifications_enabled BOOLEAN DEFAULT true;",
        # Safety: rename legacy `network` → `preferred_network` already handled above.
        # Guard: ensure no stale `network` column blocks startup on legacy DBs.
        # (No-op on clean DBs — ProgrammingError is caught and ignored.)
    ]
    for statement in _MIGRATIONS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
        except (ProgrammingError, OperationalError):
            # Column already exists — safe to ignore
            pass
