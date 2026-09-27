"""Database engine/session factories. Every query goes through an explicit transaction scope."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from crp_core.config import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(max(1, settings.database_connect_timeout_seconds))},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Open a session with a transaction that commits on success and rolls back on error."""
    async with factory() as session, session.begin():
        yield session
