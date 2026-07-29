"""Shared pytest fixtures - temp SQLite DB for tests that touch storage.

By default the suite runs against the aiosqlite backend. Set the env var
`DATABASE_BACKEND=postgres` (with `DATABASE_URL` pointing at a real Postgres
server) before invoking pytest to run the exact same suite against the
postgres adapter instead - this is how the adapter is verified without call
sites knowing which backend is active.
"""

import asyncio
import os

import pytest

from app.config import get_settings
from app.storage import database
from app.gmail import authorize

_POSTGRES_TABLES = (
    "transactions", "ingestion_state", "ingestion_runs", "counterparty_mapping",
    "unknown_patterns", "ignored_subjects", "users", "insurance_policies",
)


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    """Point app.storage.database at a fresh temp DB and initialize its schema.

    aiosqlite gets a real fresh temp file per test (just patching
    DATABASE_PATH). Postgres has no such per-test file: it's a live shared
    server, so isolation instead comes from dropping and recreating every app
    table before each test (pytest runs sequentially by default, so this is
    safe without a lock).
    """
    monkeypatch.setattr(authorize, "USER_TOKEN_ROOT", tmp_path / "gmail-users")

    if os.environ.get("DATABASE_BACKEND") == "postgres":
        from app.storage import postgres_backend

        async def _reset():
            conn = await postgres_backend.connect(
                os.environ["DATABASE_URL"], ssl=os.environ.get("DATABASE_SSL", "true").lower() != "false"
            )
            for table in _POSTGRES_TABLES:
                await conn._conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            await conn.close()
            await database.init_db()

        get_settings.cache_clear()
        asyncio.run(_reset())
        return None

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    asyncio.run(database.init_db())
    return db_path


@pytest.fixture
def db_connection(temp_db_path):
    """A single open connection to the temp DB, closed after the test."""
    db = asyncio.run(database.get_connection())
    yield db
    asyncio.run(db.close())
