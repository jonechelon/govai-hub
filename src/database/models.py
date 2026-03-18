# src/database/models.py
# Celo GovAI Hub — SQLAlchemy async models.
# Production: PostgreSQL via Neon (asyncpg driver).
# Development: SQLite fallback (set DATABASE_URL in .env).

from __future__ import annotations

import os
import re
import ssl
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


_raw_url = os.getenv("DATABASE_URL", "")

if _raw_url:
    # Remove sslmode query param — asyncpg uses ssl= kwarg instead
    _clean_url = re.sub(r"[?&]sslmode=\w+", "", _raw_url)
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
    deposit_celo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
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
