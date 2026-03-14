# src/database/models.py
# Up-to-Celo — SQLAlchemy ORM models

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """Telegram user registered with the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subscribed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Premium subscription fields ---
    tier: Mapped[str] = mapped_column(String(16), default="free")
    # Valid values: "free" | "premium"

    premium_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when user activates premium; cleared on expiry.

    wallet_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    # Celo address provided by user for payment verification.

    __table_args__ = (
        Index("ix_users_tier", "tier"),
        Index("ix_users_premium_expires_at", "premium_expires_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<User telegram_id={self.telegram_id} username={self.username!r} "
            f"tier={self.tier!r}>"
        )


class UserAppFilter(Base):
    """Tracks which apps a user has enabled/disabled in their digest."""

    __tablename__ = "user_app_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)

    def __repr__(self) -> str:
        return f"<UserAppFilter user={self.telegram_id} app={self.app_key!r} enabled={self.enabled}>"


class DigestLog(Base):
    """Records every digest sent to a user."""

    __tablename__ = "digest_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    digest_type: Mapped[str] = mapped_column(String(32), default="daily")
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<DigestLog user={self.telegram_id} type={self.digest_type!r} sent_at={self.sent_at}>"


class FetcherLog(Base):
    """Records fetcher run results for health monitoring."""

    __tablename__ = "fetcher_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetcher_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(default=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<FetcherLog fetcher={self.fetcher_name!r} "
            f"success={self.success} items={self.items_fetched}>"
        )
