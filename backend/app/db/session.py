from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine = None
_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _maker
    if _maker is None:
        _maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _maker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
