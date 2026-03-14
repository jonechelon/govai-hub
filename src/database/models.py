# src/database/models.py
# Up-to-Celo — SQLAlchemy ORM models (P23 — single source of truth)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

# ─── Engine and session factory (exposed for DatabaseManager) ─────────────────

engine = create_async_engine(
    "sqlite+aiosqlite:///data/uptocelo.db",
    echo=False,  # set to True only for debug
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ─── Declarative base ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Apps available (23 apps across 7 categories) ─────────────────────────────
# If a 24th app is added to the roadmap, update this dict and re-run init_db.

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
    "onramp": ["celocashflow", "unipos"],
    "nfts": ["octoplace", "hypermove", "truefeedback"],
    "refi": ["toucan", "toucanrefi"],
    "social": ["impactmarket", "masa"],
    "network": ["celonetwork", "celoreserve"],
}


# ─── Models ──────────────────────────────────────────────────────────────────

class User(Base):
    """Telegram user registered with the bot."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
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
    wallet_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    # Celo wallet address provided by the user for payment verification

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
        Integer,
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
        default=lambda: datetime.now(timezone.utc),
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
        default=lambda: datetime.now(timezone.utc),
    )
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    error_msg: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


# ─── Schema initialization ───────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables if they do not exist. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
