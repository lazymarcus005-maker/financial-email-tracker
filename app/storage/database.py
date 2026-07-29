"""SQLite schema and database initialization.

Supports two backends selected by `DATABASE_BACKEND`:
- "aiosqlite" (default): local SQLite file at `DATABASE_PATH`.
- "postgres": a PostgreSQL server via `app.storage.postgres_backend`, which
  exposes the same connection interface aiosqlite does, so callers
  (queries.py, persistence.py, routes) don't know which is active.

The postgres backend skips `_migrate_schema`/`migrate_owner_scope` entirely -
those exist to evolve a SQLite *file* across years of `ALTER TABLE ADD
COLUMN`s; a Postgres deployment starts from postgres_backend.SCHEMA_SQL, which
is already correct, via a one-time data migration (scripts/migrate_to_postgres.py).
"""

import aiosqlite
import logging
from pathlib import Path

from app.config import get_settings
from app.storage import postgres_backend

logger = logging.getLogger(__name__)

DATABASE_PATH = Path("data/finance.db")
SQLITE_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MS = SQLITE_TIMEOUT_SECONDS * 1000


def _use_postgres() -> bool:
    return get_settings().DATABASE_BACKEND == "postgres"


SCHEMA_SQL = """
-- Transactions from email parser
CREATE TABLE IF NOT EXISTS transactions (
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
    category_source TEXT,  -- manual, history, rule, ai, uncategorized
    bank TEXT,
    parser_version TEXT,
    parse_status TEXT,  -- complete, partial, failed, ignored
    parse_confidence REAL DEFAULT 1.0,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    gmail_message_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, transaction_id),
    UNIQUE(owner_user_id, gmail_message_id)
);

-- Ingestion state
CREATE TABLE IF NOT EXISTS ingestion_state (
    id INTEGER PRIMARY KEY,
    owner_user_id INTEGER UNIQUE,
    last_success_at DATETIME,
    last_error TEXT
);

-- Cron run history
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    run_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    emails_checked INTEGER,
    inserted INTEGER,
    duplicates INTEGER,
    failed INTEGER,
    duration_seconds REAL
);

-- Category mappings (merchant -> category)
CREATE TABLE IF NOT EXISTS counterparty_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    counterparty TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT,  -- manual, rule
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, counterparty)
);

-- Unknown/unparseable emails
CREATE TABLE IF NOT EXISTS unknown_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    subject TEXT,
    sender TEXT,
    transaction_code TEXT,
    amount REAL,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    parser_version TEXT,
    status TEXT DEFAULT 'pending',  -- pending, ignored
    gmail_message_id TEXT,
    received_at DATETIME,
    resolved_transaction_id INTEGER,
    resolved_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, gmail_message_id)
);

-- Subjects the user does not want to fetch/import again
CREATE TABLE IF NOT EXISTS ignored_subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    subject TEXT NOT NULL,
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, subject)
);

-- Application users
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_occurred_at ON transactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_transactions_transaction_id ON transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_gmail_id ON transactions(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_run_at ON ingestion_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_ignored_subjects_subject ON ignored_subjects(subject);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""


async def init_db():
    """Initialize database and schema."""
    if _use_postgres():
        db = await _connect_postgres()
        try:
            # postgres_backend.SCHEMA_SQL is already correct (native Postgres
            # dialect, all columns/constraints present) - no ALTER-history to
            # replay, so _migrate_schema/migrate_owner_scope don't apply here.
            await db.executescript(postgres_backend.SCHEMA_SQL)
        finally:
            await db.close()
        logger.info("Database initialized (postgres backend)")
        return

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DATABASE_PATH), timeout=SQLITE_TIMEOUT_SECONDS) as db:
        await configure_connection(db)
        await db.executescript(SCHEMA_SQL)
        await _migrate_schema(db)
        await migrate_owner_scope(db)
        await db.commit()
        logger.info(f"Database initialized: {DATABASE_PATH}")


async def _connect_postgres() -> postgres_backend.PostgresConnection:
    settings = get_settings()
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_BACKEND=postgres requires DATABASE_URL to be set")
    return await postgres_backend.connect(settings.DATABASE_URL, ssl=settings.DATABASE_SSL)


async def _migrate_schema(db: aiosqlite.Connection) -> None:
    """Add columns introduced after the initial schema, if not already present.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so each addition is guarded by
    checking `PRAGMA table_info` first.
    """
    cursor = await db.execute("PRAGMA table_info(transactions)")
    transaction_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "bank" not in transaction_columns:
        await db.execute("ALTER TABLE transactions ADD COLUMN bank TEXT")

    cursor = await db.execute("PRAGMA table_info(unknown_patterns)")
    unknown_columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "received_at" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN received_at DATETIME")
    if "resolved_transaction_id" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN resolved_transaction_id INTEGER")
    if "resolved_at" not in unknown_columns:
        await db.execute("ALTER TABLE unknown_patterns ADD COLUMN resolved_at DATETIME")


async def configure_connection(db: aiosqlite.Connection) -> None:
    """Apply SQLite pragmas that make web reads and ingestion writes coexist better."""
    await db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    await cursor.close()
    return {row[1] for row in rows}


async def _column_order(db: aiosqlite.Connection, table: str) -> list[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    await cursor.close()
    return [row[1] for row in rows]


# Canonical column order per SCHEMA_SQL, used to detect drift on tables where a
# later `ALTER TABLE ADD COLUMN` (which always appends at the end) left a
# column out of the position SCHEMA_SQL declares it in. `ALTER TABLE ADD
# COLUMN` runs in `_migrate_schema`, *before* the owner-scope rebuilds below;
# if a rebuild had already happened on that DB before the column existed, the
# rebuild's UNIQUE-constraint check sees "already rebuilt" and skips forever,
# permanently leaving the column trailing instead of in its SCHEMA_SQL spot.
# This doesn't break the app (every query names its columns explicitly), but
# it silently breaks anything that assumes column order, e.g. `sqlite3 .dump`
# / iterdump()-based tooling (positional `INSERT INTO t VALUES (...)`).
_TRANSACTIONS_COLUMN_ORDER = [
    "id", "owner_user_id", "transaction_id", "transaction_type", "direction", "status",
    "occurred_at", "amount", "fee", "available_balance", "counterparty", "description",
    "category", "category_source", "bank", "parser_version", "parse_status", "parse_confidence",
    "warnings_json", "raw_fields_json", "gmail_message_id", "created_at", "updated_at",
]

_UNKNOWN_PATTERNS_COLUMN_ORDER = [
    "id", "owner_user_id", "subject", "sender", "transaction_code", "amount",
    "warnings_json", "raw_fields_json", "parser_version", "status", "gmail_message_id",
    "received_at", "resolved_transaction_id", "resolved_at", "created_at",
]


async def _first_admin_id(db: aiosqlite.Connection) -> int | None:
    cursor = await db.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    row = await cursor.fetchone()
    await cursor.close()
    return row[0] if row else None


async def _table_sql(db: aiosqlite.Connection, table: str) -> str:
    cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    row = await cursor.fetchone()
    await cursor.close()
    return row[0] if row and row[0] else ""


async def _add_owner_column(db: aiosqlite.Connection, table: str) -> None:
    columns = await _columns(db, table)
    if "owner_user_id" not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN owner_user_id INTEGER")


async def _backfill_owner(db: aiosqlite.Connection, table: str) -> None:
    owner_user_id = await _first_admin_id(db)
    if owner_user_id is not None:
        await db.execute(f"UPDATE {table} SET owner_user_id = ? WHERE owner_user_id IS NULL", (owner_user_id,))


async def _rebuild_transactions(db: aiosqlite.Connection) -> None:
    sql = await _table_sql(db, "transactions")
    has_constraint = "UNIQUE(owner_user_id, gmail_message_id)" in sql
    in_canonical_order = await _column_order(db, "transactions") == _TRANSACTIONS_COLUMN_ORDER
    if has_constraint and in_canonical_order:
        return
    await db.executescript(
        """
        ALTER TABLE transactions RENAME TO transactions_legacy;
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
            bank TEXT,
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
        INSERT OR IGNORE INTO transactions (
            id, owner_user_id, transaction_id, transaction_type, direction, status,
            occurred_at, amount, fee, available_balance, counterparty, description,
            category, category_source, bank, parser_version, parse_status, parse_confidence,
            warnings_json, raw_fields_json, gmail_message_id, created_at, updated_at
        )
        SELECT
            id, owner_user_id, transaction_id, transaction_type, direction, status,
            occurred_at, amount, fee, available_balance, counterparty, description,
            category, category_source, bank, parser_version, parse_status, parse_confidence,
            warnings_json, raw_fields_json, gmail_message_id, created_at, updated_at
        FROM transactions_legacy;
        DROP TABLE transactions_legacy;
        """
    )


async def _rebuild_counterparty_mapping(db: aiosqlite.Connection) -> None:
    sql = await _table_sql(db, "counterparty_mapping")
    if "UNIQUE(owner_user_id, counterparty)" in sql:
        return
    await db.executescript(
        """
        ALTER TABLE counterparty_mapping RENAME TO counterparty_mapping_legacy;
        CREATE TABLE counterparty_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            counterparty TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_user_id, counterparty)
        );
        INSERT OR IGNORE INTO counterparty_mapping (
            id, owner_user_id, counterparty, category, source, created_at
        )
        SELECT id, owner_user_id, counterparty, category, source, created_at
        FROM counterparty_mapping_legacy;
        DROP TABLE counterparty_mapping_legacy;
        """
    )


async def _rebuild_unknown_patterns(db: aiosqlite.Connection) -> None:
    sql = await _table_sql(db, "unknown_patterns")
    has_constraint = "UNIQUE(owner_user_id, gmail_message_id)" in sql
    in_canonical_order = await _column_order(db, "unknown_patterns") == _UNKNOWN_PATTERNS_COLUMN_ORDER
    if has_constraint and in_canonical_order:
        return
    await db.executescript(
        """
        ALTER TABLE unknown_patterns RENAME TO unknown_patterns_legacy;
        CREATE TABLE unknown_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            subject TEXT,
            sender TEXT,
            transaction_code TEXT,
            amount REAL,
            warnings_json TEXT DEFAULT '[]',
            raw_fields_json TEXT,
            parser_version TEXT,
            status TEXT DEFAULT 'pending',
            gmail_message_id TEXT,
            received_at DATETIME,
            resolved_transaction_id INTEGER,
            resolved_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_user_id, gmail_message_id)
        );
        INSERT OR IGNORE INTO unknown_patterns (
            id, owner_user_id, subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, status, gmail_message_id, received_at,
            resolved_transaction_id, resolved_at, created_at
        )
        SELECT
            id, owner_user_id, subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, status, gmail_message_id, received_at,
            resolved_transaction_id, resolved_at, created_at
        FROM unknown_patterns_legacy;
        DROP TABLE unknown_patterns_legacy;
        """
    )


async def _rebuild_ignored_subjects(db: aiosqlite.Connection) -> None:
    sql = await _table_sql(db, "ignored_subjects")
    if "UNIQUE(owner_user_id, subject)" in sql:
        return
    await db.executescript(
        """
        ALTER TABLE ignored_subjects RENAME TO ignored_subjects_legacy;
        CREATE TABLE ignored_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            subject TEXT NOT NULL,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_user_id, subject)
        );
        INSERT OR IGNORE INTO ignored_subjects (id, owner_user_id, subject, reason, created_at)
        SELECT id, owner_user_id, subject, reason, created_at
        FROM ignored_subjects_legacy;
        DROP TABLE ignored_subjects_legacy;
        """
    )


async def migrate_owner_scope(db: aiosqlite.Connection) -> None:
    """Upgrade existing runtime tables from shared data to owner-scoped data."""
    for table in (
        "transactions",
        "ingestion_state",
        "ingestion_runs",
        "counterparty_mapping",
        "unknown_patterns",
        "ignored_subjects",
    ):
        await _add_owner_column(db, table)
        await _backfill_owner(db, table)

    await _rebuild_transactions(db)
    await _rebuild_counterparty_mapping(db)
    await _rebuild_unknown_patterns(db)
    await _rebuild_ignored_subjects(db)

    await db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_owner ON transactions(owner_user_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_transaction_id ON transactions(transaction_id);
        CREATE INDEX IF NOT EXISTS idx_ingestion_runs_owner ON ingestion_runs(owner_user_id);
        """
    )


async def get_connection() -> aiosqlite.Connection:
    """Get database connection."""
    if _use_postgres():
        return await _connect_postgres()

    db = await aiosqlite.connect(str(DATABASE_PATH), timeout=SQLITE_TIMEOUT_SECONDS)
    db.row_factory = aiosqlite.Row
    await configure_connection(db)
    return db
