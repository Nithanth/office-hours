"""Async database bootstrap for the private-beta persistence layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeAlias

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

import config as _cfg

SessionMaker: TypeAlias = async_sessionmaker[AsyncSession]


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


_engine: AsyncEngine | None = None
_sessionmaker: SessionMaker | None = None


def database_available() -> bool:
    return bool(_cfg.config.DATABASE_URL)


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if not database_available():
        raise RuntimeError("Database is not configured or SQLAlchemy is not installed.")
    if _engine is None:
        assert create_async_engine is not None
        assert async_sessionmaker is not None
        _engine = create_async_engine(
            _cfg.config.DATABASE_URL,
            pool_pre_ping=True,
            future=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> Any:
    if _sessionmaker is None:
        get_engine()
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    maker = get_sessionmaker()
    async with maker() as session:
        yield session


async def init_database() -> None:
    """Initialize DB connectivity and create tables in dev convenience mode."""

    if not database_available():
        logger.info("database_disabled")
        return

    from services.persistence import models  # noqa: F401 - registers metadata

    engine = get_engine()
    if _cfg.config.ENV != "prod":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_tables_ready")
    else:
        logger.info("database_ready")


async def close_database() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
