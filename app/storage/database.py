"""SQLite schema and database initialization."""

import aiosqlite
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATABASE_PATH = Path("data/finance.db")


SCHEMA_SQL = """
-- Transactions from email parser
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT UNIQUE,
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
    parser_version TEXT,
    parse_status TEXT,  -- complete, partial, failed, ignored
    parse_confidence REAL DEFAULT 1.0,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    gmail_message_id TEXT UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Ingestion state
CREATE TABLE IF NOT EXISTS ingestion_state (
    id INTEGER PRIMARY KEY,
    last_success_at DATETIME,
    last_error TEXT
);

-- Cron run history
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    counterparty TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL,
    source TEXT,  -- manual, rule
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Unknown/unparseable emails
CREATE TABLE IF NOT EXISTS unknown_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    sender TEXT,
    transaction_code TEXT,
    amount REAL,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    parser_version TEXT,
    status TEXT DEFAULT 'pending',  -- pending, ignored
    gmail_message_id TEXT UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_occurred_at ON transactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_transactions_gmail_id ON transactions(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_run_at ON ingestion_runs(run_at);
"""


async def init_db():
    """Initialize database and schema."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
        logger.info(f"Database initialized: {DATABASE_PATH}")


async def get_connection() -> aiosqlite.Connection:
    """Get database connection."""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    return db
