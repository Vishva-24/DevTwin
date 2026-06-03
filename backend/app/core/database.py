"""
app/core/database.py

SQLAlchemy async engine, session factory, and FastAPI dependency.

Design decisions:
- Uses psycopg v3 driver (psycopg[binary]) for non-blocking DB calls.
  psycopg ships pre-built binary wheels for Python 3.14, unlike asyncpg
  which requires MSVC to compile from source on Windows.
- pool_pre_ping=True ensures stale connections are dropped and replaced
  automatically (important for long-running dev servers with Docker restarts).
- Sessions are always closed in the finally block — no leaks.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# ─── Engine ───────────────────────────────────────────────────────────────────

engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "development"),  # logs SQL in dev, silent in prod
    pool_pre_ping=True,   # test connections before use (handles Docker restarts)
    pool_size=10,         # max persistent connections in the pool
    max_overflow=20,      # extra connections allowed above pool_size under load
)

# ─── Session factory ──────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep ORM objects usable after commit()
    autoflush=False,
    autocommit=False,
)

# ─── Declarative base ─────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.

    All models in app/models/ must inherit from this class so Alembic
    can discover them for auto-migration generation.
    """
    pass


# ─── FastAPI dependency ───────────────────────────────────────────────────────


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per request.

    Usage in a route:
        @router.post("/")
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            ...

    The session is always closed in the finally block, even on exceptions.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
