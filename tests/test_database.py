"""Tests for app.storage.database - schema init and additive migrations."""

import os

import pytest

from app.storage import database

_postgres_backend = os.environ.get("DATABASE_BACKEND") == "postgres"


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


@pytest.mark.skipif(
    _postgres_backend,
    reason="exercises SQLite-only rebuild internals (AUTOINCREMENT, ALTER TABLE "
    "RENAME); postgres_backend has no ALTER-history to self-heal - it starts from a "
    "correct schema on every init_db(), by design (see database.py module docstring)",
)
@pytest.mark.asyncio
async def test_column_order_self_heals_after_late_alter_table(temp_db_path):
    """Regression test for a real bug found migrating a production DB.

    `ALTER TABLE ADD COLUMN` always appends at the end. If a rebuild (which
    normalizes column order to SCHEMA_SQL) had already run *before* `bank` /
    `received_at` etc. existed, those columns end up permanently trailing
    instead of in their SCHEMA_SQL position - because the rebuild's old gate
    only checked for the UNIQUE constraint, which was already satisfied, so
    it never ran again. This doesn't break the app (every query names its
    columns), but it silently corrupts anything relying on column order, e.g.
    `sqlite3 .dump`/iterdump()-based migration tooling (positional
    `INSERT INTO t VALUES (...)`).

    Reproduces that exact history: rebuild the table into "new" shape first
    (as if the owner-scope migration already ran), *then* append the trailing
    columns the old way, and verify `init_db()` heals the order back to
    canonical on the next run - without touching another `init_db()` call.
    """
    db = await database.get_connection()

    # Simulate: rebuild already happened (UNIQUE constraint present), but
    # without `bank` yet - mirrors a DB migrated before `bank` was introduced.
    # temp_db_path already ran the (fixed) init_db(), so the starting table
    # already has `bank`; name columns explicitly rather than SELECT * so the
    # narrower "old shape" table (without bank) can still copy from it.
    await db.executescript(
        """
        ALTER TABLE transactions RENAME TO transactions_old;
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            transaction_id TEXT,
            transaction_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            occurred_at DATETIME NOT NULL,
            amount REAL NOT NULL,
            fee REAL DEFAULT 0.0,
            available_balance REAL,
            counterparty TEXT,
            description TEXT,
            category TEXT,
            category_source TEXT,
            parser_version TEXT,
            parse_status TEXT,
            parse_confidence REAL DEFAULT 1.0,
            warnings_json TEXT DEFAULT '[]',
            raw_fields_json TEXT,
            gmail_message_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_user_id, transaction_id),
            UNIQUE(owner_user_id, gmail_message_id)
        );
        INSERT INTO transactions (
            id, owner_user_id, transaction_id, transaction_type, direction, status,
            occurred_at, amount, fee, available_balance, counterparty, description,
            category, category_source, parser_version, parse_status, parse_confidence,
            warnings_json, raw_fields_json, gmail_message_id, created_at, updated_at
        )
        SELECT
            id, owner_user_id, transaction_id, transaction_type, direction, status,
            occurred_at, amount, fee, available_balance, counterparty, description,
            category, category_source, parser_version, parse_status, parse_confidence,
            warnings_json, raw_fields_json, gmail_message_id, created_at, updated_at
        FROM transactions_old;
        DROP TABLE transactions_old;
        """
    )
    # ... then `bank` gets added the old way: appended at the end, like a real
    # `ALTER TABLE ADD COLUMN` would on a DB that already had the constraint.
    await db.execute("ALTER TABLE transactions ADD COLUMN bank TEXT")
    await db.execute(
        "INSERT INTO transactions (transaction_type, direction, status, occurred_at, amount, "
        "gmail_message_id, bank) VALUES ('transfer', 'out', 'success', '2026-01-01', 100.0, 'msg-1', 'kbank')"
    )
    await db.commit()

    cursor = await db.execute("PRAGMA table_info(transactions)")
    drifted_order = [row[1] for row in await cursor.fetchall()]
    await cursor.close()
    assert drifted_order != database._TRANSACTIONS_COLUMN_ORDER, "test setup should reproduce the drift"
    assert drifted_order[-1] == "bank"
    await db.close()

    # The next init_db() (e.g. next app startup) must self-heal the order.
    await database.init_db()

    db = await database.get_connection()
    cursor = await db.execute("PRAGMA table_info(transactions)")
    healed_order = [row[1] for row in await cursor.fetchall()]
    await cursor.close()
    assert healed_order == database._TRANSACTIONS_COLUMN_ORDER

    # Data must survive the rebuild untouched.
    cursor = await db.execute("SELECT bank, amount, gmail_message_id FROM transactions WHERE gmail_message_id = 'msg-1'")
    row = await cursor.fetchone()
    await cursor.close()
    assert tuple(row) == ("kbank", 100.0, "msg-1")

    # And it must be a fixed point: running init_db() again must not re-trigger
    # the rebuild (no infinite churn / no more DROP+CREATE than necessary).
    await database.init_db()
    cursor = await db.execute("PRAGMA table_info(transactions)")
    assert [row[1] for row in await cursor.fetchall()] == database._TRANSACTIONS_COLUMN_ORDER
    await cursor.close()
    await db.close()
