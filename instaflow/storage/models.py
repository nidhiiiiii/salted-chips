"""
ORM Models — Module 6.1

All tables defined per the architecture spec.
Uses SQLAlchemy 2.0 declarative style with mapped_column.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared base for all ORM models."""
    pass


class Account(Base):
    """Instagram accounts managed by the platform."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ig_username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    session_file: Mapped[Optional[str]] = mapped_column(String(255))
    proxy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proxies.id"))
    health_score: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active",
        comment="active | quarantine | banned",
    )
    country: Mapped[str] = mapped_column(String(10), default="IN", server_default="IN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    proxy: Mapped[Optional["Proxy"]] = relationship(back_populates="accounts")
    follows: Mapped[list["Follow"]] = relationship(back_populates="account")
    task_logs: Mapped[list["TaskLog"]] = relationship(back_populates="account")


class Proxy(Base):
    """Residential proxy pool."""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(100), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active",
        comment="active | cooling | degraded | retired",
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    accounts: Mapped[list["Account"]] = relationship(back_populates="proxy")


class Reel(Base):
    """Reel URLs submitted for engagement processing."""

    __tablename__ = "reels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    media_id: Mapped[Optional[str]] = mapped_column(String(100))
    creator_username: Mapped[Optional[str]] = mapped_column(String(50))
    creator_user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Follow tracking
    follow_status: Mapped[Optional[str]] = mapped_column(
        String(30),
        comment="skipped | followed | already_following | private_pending",
    )
    followed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Comment tracking — always "link"
    comment_text: Mapped[Optional[str]] = mapped_column(Text)
    comment_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Job lifecycle
    job_status: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending",
        index=True,
        comment="pending | follow_pending | commenting | completed | failed | challenge_pause",
    )

    # Relationships
    dm_messages: Mapped[list["DmMessage"]] = relationship(back_populates="reel")
    task_logs: Mapped[list["TaskLog"]] = relationship(back_populates="reel")


class DmMessage(Base):
    """Incoming DMs from creators being watched."""

    __tablename__ = "dm_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reels.id"), index=True)
    creator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    message_text: Mapped[Optional[str]] = mapped_column(Text)
    cta_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    cta_confidence: Mapped[Optional[float]] = mapped_column(Float)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    reel: Mapped[Optional["Reel"]] = relationship(back_populates="dm_messages")
    extracted_links: Mapped[list["ExtractedLink"]] = relationship(back_populates="dm_message")


class ExtractedLink(Base):
    """Final resolved URLs extracted from creator DMs."""

    __tablename__ = "extracted_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dm_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("dm_messages.id"), index=True)
    original_url: Mapped[Optional[str]] = mapped_column(Text)
    redirect_chain: Mapped[Optional[dict]] = mapped_column(JSONB)
    final_url: Mapped[Optional[str]] = mapped_column(Text)
    extraction_method: Mapped[Optional[str]] = mapped_column(
        String(20), comment="requests | playwright"
    )
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    exported_to_excel: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    dm_message: Mapped[Optional["DmMessage"]] = relationship(back_populates="extracted_links")


class Follow(Base):
    """Follow relationships created by the bot."""

    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    creator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_username: Mapped[Optional[str]] = mapped_column(String(50))
    followed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    follow_back: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_back_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    account: Mapped["Account"] = relationship(back_populates="follows")


class TaskLog(Base):
    """Audit log for all Celery task executions."""

    __tablename__ = "task_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    task_type: Mapped[Optional[str]] = mapped_column(String(50))
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id"), index=True)
    reel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reels.id"))
    status: Mapped[Optional[str]] = mapped_column(String(20))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    account: Mapped[Optional["Account"]] = relationship(back_populates="task_logs")
    reel: Mapped[Optional["Reel"]] = relationship(back_populates="task_logs")
