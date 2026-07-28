"""Tests for app.storage.database - schema init and additive migrations."""

import pytest

from app.storage import database


@pytest.mark.asyncio
async def test_init_db_creates_new_columns(temp_db_path):
    db = await database.get_connection()
    cursor = await db.execute("PRAGMA table_info(transactions)")
    transaction_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    cursor = await db.execute("PRAGMA table_info(unknown_patterns)")
    unknown_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    await db.close()

    assert "bank" in transaction_columns
    assert {"received_at", "resolved_transaction_id", "resolved_at"} <= unknown_columns


@pytest.mark.asyncio
async def test_migrate_schema_is_idempotent(temp_db_path):
    db = await database.get_connection()
    # Must not raise - SQLite errors on ALTER TABLE ADD COLUMN for a column
    # that already exists, so re-running migration on an up-to-date DB has
    # to be a no-op.
    await database._migrate_schema(db)
    await db.commit()
    await db.close()
