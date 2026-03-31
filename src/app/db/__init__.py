"""Database engine, session factory, and declarative Base."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _make_async_url(url: str) -> str:
    """Convert postgresql:// or postgres:// to postgresql+asyncpg:// for async driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


if settings.database_url:
    _async_url = _make_async_url(settings.database_url)
    engine = create_async_engine(_async_url, echo=False, pool_recycle=300, pool_pre_ping=True, pool_size=5, max_overflow=10)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
else:
    engine = None
    async_session_factory = None


async def get_db():
    """FastAPI dependency: yield an AsyncSession, or None when no DB is configured."""
    if async_session_factory is None:
        yield None
        return
    async with async_session_factory() as session:
        yield session
