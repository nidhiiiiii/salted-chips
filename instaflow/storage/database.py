"""
SQLAlchemy Async Engine & Session Factory — Module 6

Provides a single engine (connection pool) and a session factory for
the entire application.  All modules import `get_db_session` and use
it as an async context manager.

Usage::

    async with get_db_session() as session:
        result = await session.execute(select(Reel))
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from instaflow.config.logging import get_logger
from instaflow.config.settings import get_settings

logger = get_logger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,  # Recycle connections every 30 min
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager yielding a transactional DB session.

    Commits on clean exit, rolls back on exception.
    """
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """
    Create all tables (development shortcut).
    In production use Alembic migrations instead.
    """
    from instaflow.storage.models import Base

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database.tables_created")


async def close_db() -> None:
    """Dispose the engine (call on shutdown)."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database.closed")
