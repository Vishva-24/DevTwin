"""
alembic/env.py

Alembic migration environment — configured for async SQLAlchemy.

Key differences from the default alembic env.py:
  1. DATABASE_URL is read from pydantic-settings (not from alembic.ini).
     This keeps secrets out of version control.
  2. Uses run_async_migrations() because our engine is async (psycopg v3).
  3. Imports all ORM models via app.models so Alembic can auto-generate
     migrations from the current model definitions.
  4. Forces WindowsSelectorEventLoopPolicy on Windows — psycopg v3 async
     does not support the ProactorEventLoop that Python 3.8+ uses by default.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Path setup ────────────────────────────────────────────────────────────────
# Ensure the `backend/` directory is on sys.path so `app.*` imports work
# when running `alembic` from the backend/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Import models + settings ──────────────────────────────────────────────────
# IMPORTANT: importing app.models here ensures all ORM models are registered
# on Base.metadata before Alembic tries to auto-generate a migration.
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402, F401  — registers Repository, Analysis on Base

settings = get_settings()

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

# Inject the real DATABASE_URL from pydantic-settings into Alembic's config.
# This overrides the placeholder value in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up Python logging from the alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The target metadata for --autogenerate support.
target_metadata = Base.metadata


# ── Migration runners ─────────────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Generates SQL without needing a live database connection.
    Useful for reviewing what Alembic would do, or for DBAs who
    apply migrations manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations synchronously on an existing connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detect column type changes
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode using an async engine.

    asyncpg requires the async runner because it cannot be used with the
    standard synchronous Alembic connection pool.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # no pooling for migration runs
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    # psycopg v3 async does not support Windows ProactorEventLoop.
    # Force SelectorEventLoop on Windows (same behaviour as Linux/macOS).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined]
        )
    asyncio.run(run_async_migrations())


# ── Dispatch ──────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
