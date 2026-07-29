"""One-shot migration: copy an aiosqlite `finance.db` into a PostgreSQL target.

Usage:
    python scripts/migrate_to_postgres.py --source data/finance.db \
        --target-url postgresql://user:pass@host:5432/dbname [--no-ssl]

IMPORTANT: always run this against a COPY of the source DB first (see
docs/postgres-migration-plan.md) and verify counts before touching production
data. This script itself never writes to --source; it only reads.

Why this maps rows by COLUMN NAME rather than a positional dump: Postgres is a
different SQL dialect than SQLite, so there is no "replay the source's own
CREATE TABLE" option here - postgres_backend.SCHEMA_SQL (a from-scratch,
already-correct Postgres schema) is created first via `database.init_db()`,
and each row is then inserted by explicit column name. This is correct
regardless of whether the source and target schemas happen to agree on
column order, since named-column INSERTs don't rely on it.

IDENTITY columns: every table's `id` uses `GENERATED ALWAYS AS IDENTITY`,
which normally rejects explicit id values. Preserving the original ids matters
here because `unknown_patterns.resolved_transaction_id` points at
`transactions.id` - re-numbering rows would silently break that link. Each
INSERT uses `OVERRIDING SYSTEM VALUE` to insert the original id, and every
table's identity sequence is bumped past the max inserted id afterward so
future auto-generated ids don't collide.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage import postgres_backend  # noqa: E402

TABLES = (
    "users",
    "transactions",
    "ingestion_state",
    "ingestion_runs",
    "counterparty_mapping",
    "unknown_patterns",
    "ignored_subjects",
    "insurance_policies",
)


async def migrate(source_path: Path, target_url: str, ssl: bool) -> None:
    src = sqlite3.connect(str(source_path))
    src.row_factory = sqlite3.Row

    target = await postgres_backend.connect(target_url, ssl=ssl)
    print("Creating schema (if not already present)...")
    await target.executescript(postgres_backend.SCHEMA_SQL)

    inserted = {}
    for table in TABLES:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        count = 0
        for row in rows:
            columns = row.keys()
            placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
            column_sql = ", ".join(columns)
            values = [row[c] for c in columns]
            overriding = " OVERRIDING SYSTEM VALUE" if "id" in columns else ""
            await target._conn.execute(
                f"INSERT INTO {table} ({column_sql}){overriding} VALUES ({placeholders})",
                *values,
            )
            count += 1
        inserted[table] = count
        print(f"  {table}: inserted {count} rows")

        if "id" in (rows[0].keys() if rows else []):
            await target._conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )

    print("\nVerifying row counts and full row data...")
    mismatches = []
    for table in TABLES:
        src_count = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tgt_count = await target._conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        status = "OK" if src_count == tgt_count else "MISMATCH"
        if src_count != tgt_count:
            mismatches.append(table)
        print(f"  {table}: source={src_count} target={tgt_count} [{status}]")

    for table in TABLES:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall()]
        col_sql = ", ".join(cols)
        src_rows = [tuple(r) for r in src.execute(f"SELECT {col_sql} FROM {table} ORDER BY id").fetchall()]
        tgt_records = await target._conn.fetch(f"SELECT {col_sql} FROM {table} ORDER BY id")
        tgt_rows = [tuple(r) for r in tgt_records]
        if src_rows != tgt_rows:
            mismatches.append(f"{table} (row data)")

    src.close()
    await target.close()

    if mismatches:
        print(f"\nFAILED verification: {mismatches}")
        raise SystemExit(1)
    print("\nMigration verified: all tables match source row-for-row.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Path to the source aiosqlite finance.db")
    parser.add_argument("--target-url", required=True, help="Target Postgres URL: postgresql://user:pass@host:port/db")
    parser.add_argument("--no-ssl", action="store_true", help="Disable TLS - dev-only, never for real financial data")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source DB not found: {args.source}")

    asyncio.run(migrate(args.source, args.target_url, ssl=not args.no_ssl))


if __name__ == "__main__":
    main()
