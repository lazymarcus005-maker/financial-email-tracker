"""PostgreSQL backend: an adapter that mimics the slice of the `aiosqlite`
interface this codebase uses, backed by `asyncpg`.

Postgres does not speak SQLite's dialect, so this adapter does real
translation work:
- `?` positional placeholders -> asyncpg's `$1, $2, ...`.
- No `lastrowid` in Postgres/asyncpg: every bare `INSERT` gets `RETURNING id`
  auto-appended (every table in this schema has `id` as its primary key), and
  the returned id becomes `cursor.lastrowid`. A conflict that writes no row
  (`ON CONFLICT ... DO NOTHING`) yields no RETURNING row, so lastrowid is 0 -
  matching the aiosqlite UPSERT-conflict behavior callers already handle (see
  queries.py's mapping-conflict fallback).
- SQLite-only statement shapes used at runtime are translated: `INSERT OR
  IGNORE INTO unknown_patterns (...)` and the generic `INSERT OR REPLACE INTO
  {table} (...)` used by data import, plus `PRAGMA table_info(...)` (import
  column discovery) and `PRAGMA foreign_keys = OFF` (schema here has no FK
  constraints, so it's a no-op).
- SQLite's `col IS ?` (NULL-safe equality) is rewritten to Postgres's
  `IS NOT DISTINCT FROM ?` - Postgres's `IS` only accepts a fixed keyword
  (NULL/TRUE/...), not a bound parameter, so `IS ?` is a hard syntax error.
- Every date/time column here is `TEXT`, not `TIMESTAMP`. SQLite has no real
  datetime type (SCHEMA_SQL's `DATETIME` is just an affinity; values are
  stored and returned as whatever ISO string the app wrote), and the app
  passes/reads these fields as strings throughout (parsers, templates,
  history). asyncpg is strict: a `TIMESTAMP` column rejects a bound `str`
  outright. Using `TEXT` keeps behavior identical to aiosqlite instead of
  introducing datetime objects only on Postgres.

What this backend deliberately does NOT have: the `_migrate_schema`/
`_rebuild_*` machinery in database.py. That machinery exists to evolve a
SQLite file across years of `ALTER TABLE ADD COLUMN`s on a single physical
file. A Postgres deployment starts from a one-time data migration (see
scripts/migrate_to_postgres.py) against a schema that is already correct -
there's no historical drift to heal.
"""

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import unquote

import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    transaction_id TEXT,
    transaction_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION DEFAULT 0.0,
    available_balance DOUBLE PRECISION,
    counterparty TEXT,
    description TEXT,
    category TEXT,
    category_source TEXT,
    bank TEXT,
    parser_version TEXT,
    parse_status TEXT,
    parse_confidence DOUBLE PRECISION DEFAULT 1.0,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    gmail_message_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, transaction_id),
    UNIQUE(owner_user_id, gmail_message_id)
);

CREATE TABLE IF NOT EXISTS ingestion_state (
    id INTEGER PRIMARY KEY,
    owner_user_id INTEGER UNIQUE,
    last_success_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    emails_checked INTEGER,
    inserted INTEGER,
    duplicates INTEGER,
    failed INTEGER,
    duration_seconds DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS counterparty_mapping (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    counterparty TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, counterparty)
);

CREATE TABLE IF NOT EXISTS unknown_patterns (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    subject TEXT,
    sender TEXT,
    transaction_code TEXT,
    amount DOUBLE PRECISION,
    warnings_json TEXT DEFAULT '[]',
    raw_fields_json TEXT,
    parser_version TEXT,
    status TEXT DEFAULT 'pending',
    gmail_message_id TEXT,
    received_at TEXT,
    resolved_transaction_id BIGINT,
    resolved_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, gmail_message_id)
);

CREATE TABLE IF NOT EXISTS ignored_subjects (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    subject TEXT NOT NULL,
    reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, subject)
);

CREATE TABLE IF NOT EXISTS insurance_policies (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_user_id INTEGER,
    insurer_name TEXT NOT NULL,
    policy_name TEXT NOT NULL,
    policy_number TEXT,
    policy_type TEXT DEFAULT 'other',
    insured_person TEXT,
    logo_url TEXT,
    premium_amount DOUBLE PRECISION,
    premium_frequency TEXT DEFAULT 'annual',
    coverage_amount DOUBLE PRECISION,
    start_date TEXT,
    end_date TEXT,
    renewal_date TEXT,
    status TEXT DEFAULT 'active',
    contact_phone TEXT,
    contact_email TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_occurred_at ON transactions(occurred_at);
CREATE INDEX IF NOT EXISTS idx_transactions_transaction_id ON transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_gmail_id ON transactions(gmail_message_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_transactions_owner ON transactions(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_run_at ON ingestion_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_owner ON ingestion_runs(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_ignored_subjects_subject ON ignored_subjects(subject);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""

_ALL_TABLES = (
    "transactions",
    "ingestion_state",
    "ingestion_runs",
    "counterparty_mapping",
    "unknown_patterns",
    "ignored_subjects",
    "users",
    "insurance_policies",
)


def _convert_value(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _split_dsn(dsn: str) -> dict:
    """Parse a `postgresql://user:password@host:port/db` DSN tolerantly.

    Real-world panels sometimes hand out passwords containing characters that
    are reserved in URI syntax (e.g. `#`, which starts a fragment per RFC
    3986) without percent-encoding them - both `urllib.parse.urlsplit` and
    asyncpg's own DSN parser mis-split those. This locates the userinfo/host
    boundary using the *last* `@` before the path (the only position that's
    unambiguous regardless of what characters the password contains) instead
    of relying on a strict URI parser.
    """
    scheme_idx = dsn.find("://")
    if scheme_idx == -1:
        raise ValueError(f"Not a DSN URL: {dsn!r}")
    rest = dsn[scheme_idx + 3 :]

    if "/" in rest:
        authority, _, database = rest.partition("/")
        database = database.split("?")[0] or None
    else:
        authority, database = rest, None

    user = password = None
    if "@" in authority:
        userinfo, hostpart = authority.rsplit("@", 1)
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
        else:
            user = userinfo
    else:
        hostpart = authority

    if ":" in hostpart:
        host, port_str = hostpart.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = hostpart, 5432

    return {
        "host": host,
        "port": port,
        "user": unquote(user) if user else None,
        "password": unquote(password) if password else None,
        "database": unquote(database) if database else None,
    }


# --- Dialect translation for the small, known set of SQLite-only statement
# shapes this app issues at runtime (not the schema/migration SQL, which this
# module writes in native Postgres syntax already). ------------------------

_INSERT_OR_IGNORE_UNKNOWN_PATTERNS_RE = re.compile(
    r"INSERT OR IGNORE INTO unknown_patterns", re.IGNORECASE
)
_INSERT_OR_REPLACE_RE = re.compile(
    r"INSERT OR REPLACE INTO\s+(\w+)\s*\(([^)]+)\)", re.IGNORECASE
)
_PRAGMA_TABLE_INFO_RE = re.compile(r"PRAGMA\s+table_info\((\w+)\)", re.IGNORECASE)
_PRAGMA_RE = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)
_INSERT_RE = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)
_IS_PARAM_RE = re.compile(r"\bIS\s+\?", re.IGNORECASE)
_SQLITE_DATE_FN_RE = re.compile(r"\bdate\((\w+)\)", re.IGNORECASE)


def _translate_insert_or_replace(sql: str) -> str:
    match = _INSERT_OR_REPLACE_RE.search(sql)
    if not match:
        return sql
    columns = [c.strip() for c in match.group(2).split(",")]
    update_cols = [c for c in columns if c.lower() != "id"]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO", 1)
    return f"{sql} ON CONFLICT (id) DO UPDATE SET {set_clause}"


def _translate_dialect(sql: str) -> str:
    if _INSERT_OR_IGNORE_UNKNOWN_PATTERNS_RE.search(sql):
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1)
        sql = f"{sql} ON CONFLICT (owner_user_id, gmail_message_id) DO NOTHING"
    elif "INSERT OR REPLACE INTO" in sql.upper():
        sql = _translate_insert_or_replace(sql)
    # SQLite's `col IS ?` is a NULL-safe equality comparison (matches when both
    # sides are NULL, unlike `=`). Postgres's `IS` only accepts a fixed keyword
    # (NULL/TRUE/FALSE/UNKNOWN) on the right - a bound parameter is a hard
    # syntax error there. `IS NOT DISTINCT FROM` is Postgres's NULL-safe
    # equality operator and *does* accept a parameter, with the same semantics.
    sql = _IS_PARAM_RE.sub("IS NOT DISTINCT FROM ?", sql)
    # SQLite's `date(col)` truncates an ISO datetime string down to 'YYYY-MM-DD'
    # and returns a plain string (SQLite has no real date type). Postgres's
    # `date(col)` instead CASTS to the native `date` type - which then makes
    # Postgres infer any `= ?`/`BETWEEN ? AND ?` parameter compared against it
    # as a `date` too, and asyncpg rejects a plain Python `str` for that (it
    # wants a real `datetime.date`, raising "'str' object has no attribute
    # 'toordinal'"). Since every date/time column here is TEXT storing ISO
    # strings (see module docstring), `LEFT(col, 10)` reproduces SQLite's
    # string-truncation behavior exactly while keeping everything in TEXT-land,
    # so bound string params keep working.
    sql = _SQLITE_DATE_FN_RE.sub(r"LEFT(\1, 10)", sql)
    return sql


def _maybe_inject_returning_id(sql: str) -> str:
    if _INSERT_RE.match(sql) and not _RETURNING_RE.search(sql):
        return f"{sql} RETURNING id"
    return sql


def _translate_placeholders(sql: str, params):
    """`?` positional or `:name` dict params -> asyncpg's `$1, $2, ...`."""
    if isinstance(params, dict):
        ordered_args = []

        def _sub_named(m):
            ordered_args.append(_convert_value(params[m.group(1)]))
            return f"${len(ordered_args)}"

        sql = re.sub(r":(\w+)", _sub_named, sql)
        return sql, ordered_args

    if params is None:
        return sql, []

    args = [_convert_value(v) for v in params]
    counter = iter(range(1, len(args) + 1))

    def _sub_positional(_m):
        return f"${next(counter)}"

    sql = re.sub(r"\?", _sub_positional, sql)
    return sql, args


class _Cursor:
    def __init__(self, rows: list, lastrowid: int = 0, rowcount: int = -1):
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    async def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    async def fetchall(self):
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _pragma_table_info_rows(columns: list[asyncpg.Record]) -> list[tuple]:
    # Shape-compatible with SQLite's PRAGMA table_info: callers in this repo
    # only ever read index 1 (column name), so the rest are filler values.
    return [(i, r["column_name"], r["data_type"], 0, None, 0) for i, r in enumerate(columns)]


class PostgresConnection:
    """aiosqlite-shaped facade over a single asyncpg Connection."""

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn
        self.row_factory = None

    async def execute(self, sql: str, params=None) -> _Cursor:
        pragma_match = _PRAGMA_TABLE_INFO_RE.match(sql.strip())
        if pragma_match:
            table = pragma_match.group(1)
            records = await self._conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 ORDER BY ordinal_position",
                table,
            )
            return _Cursor(_pragma_table_info_rows(records))
        if _PRAGMA_RE.match(sql):
            # No FK constraints in this schema, and WAL/synchronous/busy_timeout
            # are SQLite-only - every other PRAGMA this app issues is a no-op here.
            return _Cursor([])

        sql = _translate_dialect(sql)
        sql, args = _translate_placeholders(sql, params)
        sql = _maybe_inject_returning_id(sql)

        if _RETURNING_RE.search(sql):
            records = await self._conn.fetch(sql, *args)
            lastrowid = records[0]["id"] if records else 0
            return _Cursor(list(records), lastrowid=lastrowid, rowcount=len(records))

        status = await self._conn.execute(sql, *args)
        rowcount = _parse_rowcount(status)
        if sql.strip().upper().startswith("SELECT"):
            records = await self._conn.fetch(sql, *args)
            return _Cursor(list(records), rowcount=len(records))
        return _Cursor([], rowcount=rowcount)

    async def executemany(self, sql: str, seq_of_params) -> None:
        seq_of_params = list(seq_of_params)
        if not seq_of_params:
            return
        sql = _translate_dialect(sql)
        translated_sql, _ = _translate_placeholders(sql, seq_of_params[0])
        args_list = [_translate_placeholders(sql, p)[1] for p in seq_of_params]
        await self._conn.executemany(translated_sql, args_list)

    async def executescript(self, script: str) -> None:
        # asyncpg's simple-query protocol (execute() with no args) natively
        # runs multi-statement scripts - no manual splitting needed. Only used
        # for this module's own native-Postgres SCHEMA_SQL, never for
        # dialect-translated runtime queries.
        await self._conn.execute(script)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        await self._conn.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


def _parse_rowcount(status: str) -> int:
    # asyncpg's execute() returns a command tag like "INSERT 0 1" / "UPDATE 3".
    parts = status.split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return -1


async def connect(url: str, ssl: bool = True, timeout: float = 10.0) -> PostgresConnection:
    """Open a Postgres connection. `ssl=False` is only for known-insecure dev
    endpoints (see docs/libsql-migration-plan.md) - production must use TLS.

    `statement_timeout` is set server-side so a query that would otherwise
    block forever (e.g. waiting on a lock held by a leaked/orphaned session -
    hit for real while developing this adapter against a shared dev server)
    gets killed instead of hanging the whole process.
    """
    creds = _split_dsn(url)
    conn = await asyncpg.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["user"],
        password=creds["password"],
        database=creds["database"],
        ssl="require" if ssl else None,
        timeout=timeout,
        command_timeout=timeout,
        server_settings={"statement_timeout": str(int(timeout * 1000))},
    )
    return PostgresConnection(conn)
