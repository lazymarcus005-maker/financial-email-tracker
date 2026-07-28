"""Reusable DB query helpers backing the web routes."""

import json
import logging
from datetime import date, datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _clamp_page_size(page_size: int) -> int:
    return max(1, min(page_size, MAX_PAGE_SIZE))


def _row_to_transaction(row: aiosqlite.Row) -> dict:
    data = dict(row)
    data["raw_fields"] = json.loads(data.pop("raw_fields_json") or "{}")
    data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
    occurred_at = data.get("occurred_at") or ""
    normalized = str(occurred_at).replace("T", " ")
    data["occurred_date"] = normalized[:10] if normalized else ""
    data["occurred_time"] = normalized[11:16] if len(normalized) >= 16 else ""
    return data


def _row_to_unknown(row: aiosqlite.Row) -> dict:
    data = dict(row)
    data["raw_fields"] = json.loads(data.pop("raw_fields_json") or "{}")
    data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
    return data


# ---- Transactions ----------------------------------------------------------

async def list_transactions(
    db: aiosqlite.Connection,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    transaction_type: str | None = None,
    direction: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    sort_dir: str | None = None,
) -> tuple[list[dict], int]:
    """Return (rows, total_count) for the paginated/filtered transaction list."""
    page = max(1, page)
    page_size = _clamp_page_size(page_size)

    where = []
    params: list = []

    if date_from:
        where.append("occurred_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("occurred_at <= ?")
        params.append(date_to)
    if category:
        where.append("category = ?")
        params.append(category)
    if transaction_type:
        where.append("transaction_type = ?")
        params.append(transaction_type)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if search:
        where.append("(counterparty LIKE ? OR description LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_cursor = await db.execute(f"SELECT COUNT(*) AS n FROM transactions {where_sql}", params)
    total = (await count_cursor.fetchone())["n"]
    await count_cursor.close()

    # Sort
    allowed_sorts = {"occurred_at", "amount", "counterparty", "category", "transaction_type", "direction"}
    if sort and sort in allowed_sorts:
        order = "ASC" if sort_dir and sort_dir.upper() == "asc" else "DESC"
        order_clause = f"ORDER BY {sort} {order}, id DESC"
    else:
        order_clause = "ORDER BY occurred_at DESC, id DESC"

    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"""
        SELECT * FROM transactions {where_sql} {order_clause}
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    )
    rows = await cursor.fetchall()
    await cursor.close()

    return [_row_to_transaction(r) for r in rows], total


async def get_transaction(db: aiosqlite.Connection, transaction_id: int) -> dict | None:
    cursor = await db.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_transaction(row) if row else None


async def update_transaction_category(
    db: aiosqlite.Connection, transaction_id: int, category: str, category_source: str = "manual"
) -> None:
    await db.execute(
        """
        UPDATE transactions
        SET category = ?, category_source = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (category, category_source, transaction_id),
    )
    await db.commit()


async def set_transaction_ignored(db: aiosqlite.Connection, transaction_id: int, ignored: bool) -> None:
    parse_status = "ignored" if ignored else "complete"
    await db.execute(
        "UPDATE transactions SET parse_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (parse_status, transaction_id),
    )
    await db.commit()


async def delete_transaction(db: aiosqlite.Connection, transaction_id: int) -> None:
    await db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    await db.commit()


async def get_transaction_id_by_gmail_message_id(db: aiosqlite.Connection, gmail_message_id: str) -> int | None:
    cursor = await db.execute("SELECT id FROM transactions WHERE gmail_message_id = ?", (gmail_message_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return row["id"] if row else None


# ---- Unknown patterns -------------------------------------------------------

async def list_unknown(
    db: aiosqlite.Connection,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
) -> tuple[list[dict], int]:
    page = max(1, page)
    page_size = _clamp_page_size(page_size)

    where = []
    params: list = []
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_cursor = await db.execute(f"SELECT COUNT(*) AS n FROM unknown_patterns {where_sql}", params)
    total = (await count_cursor.fetchone())["n"]
    await count_cursor.close()

    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"""
        SELECT * FROM unknown_patterns {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [_row_to_unknown(r) for r in rows], total


async def get_unknown(db: aiosqlite.Connection, unknown_id: int) -> dict | None:
    cursor = await db.execute("SELECT * FROM unknown_patterns WHERE id = ?", (unknown_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_unknown(row) if row else None


async def set_unknown_status(db: aiosqlite.Connection, unknown_id: int, status: str) -> None:
    await db.execute("UPDATE unknown_patterns SET status = ? WHERE id = ?", (status, unknown_id))
    await db.commit()


async def delete_unknown(db: aiosqlite.Connection, unknown_id: int) -> None:
    await db.execute("DELETE FROM unknown_patterns WHERE id = ?", (unknown_id,))
    await db.commit()


# ---- Counterparty mappings ---------------------------------------------------

async def list_mappings(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute("SELECT * FROM counterparty_mapping ORDER BY counterparty ASC")
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows]


async def get_mapping(db: aiosqlite.Connection, mapping_id: int) -> dict | None:
    cursor = await db.execute("SELECT * FROM counterparty_mapping WHERE id = ?", (mapping_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def create_mapping(
    db: aiosqlite.Connection, counterparty: str, category: str, source: str = "manual"
) -> dict:
    cursor = await db.execute(
        """
        INSERT INTO counterparty_mapping (counterparty, category, source)
        VALUES (?, ?, ?)
        ON CONFLICT(counterparty) DO UPDATE SET category = excluded.category, source = excluded.source
        """,
        (counterparty, category, source),
    )
    await db.commit()
    mapping_id = cursor.lastrowid
    row = await get_mapping(db, mapping_id)
    if row:
        return row
    # Conflict path: lastrowid is unreliable on UPSERT, look up by counterparty instead.
    cursor = await db.execute("SELECT * FROM counterparty_mapping WHERE counterparty = ?", (counterparty,))
    fetched = await cursor.fetchone()
    await cursor.close()
    return dict(fetched)


async def update_mapping(db: aiosqlite.Connection, mapping_id: int, category: str) -> None:
    await db.execute("UPDATE counterparty_mapping SET category = ? WHERE id = ?", (category, mapping_id))
    await db.commit()


async def delete_mapping(db: aiosqlite.Connection, mapping_id: int) -> None:
    await db.execute("DELETE FROM counterparty_mapping WHERE id = ?", (mapping_id,))
    await db.commit()


# ---- Ingestion runs ----------------------------------------------------------

async def list_runs(db: aiosqlite.Connection, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> tuple[list[dict], int]:
    page = max(1, page)
    page_size = _clamp_page_size(page_size)

    count_cursor = await db.execute("SELECT COUNT(*) AS n FROM ingestion_runs")
    total = (await count_cursor.fetchone())["n"]
    await count_cursor.close()

    offset = (page - 1) * page_size
    cursor = await db.execute(
        "SELECT * FROM ingestion_runs ORDER BY run_at DESC, id DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows], total


async def get_run(db: aiosqlite.Connection, run_id: int) -> dict | None:
    cursor = await db.execute("SELECT * FROM ingestion_runs WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def get_last_sync(db: aiosqlite.Connection) -> str | None:
    cursor = await db.execute("SELECT MAX(run_at) AS last_run FROM ingestion_runs")
    row = await cursor.fetchone()
    await cursor.close()
    return row["last_run"] if row else None


# ---- Dashboard stats ----------------------------------------------------------

async def list_categories(db: aiosqlite.Connection) -> list[str]:
    """Return distinct non-null categories from transactions, sorted."""
    cursor = await db.execute(
        "SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL AND category != '' ORDER BY category ASC"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [r["category"] for r in rows]


async def list_transaction_types(db: aiosqlite.Connection) -> list[str]:
    """Return distinct non-null transaction types from transactions, sorted."""
    cursor = await db.execute(
        "SELECT DISTINCT transaction_type FROM transactions WHERE transaction_type IS NOT NULL AND transaction_type != '' ORDER BY transaction_type ASC"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [r["transaction_type"] for r in rows]


async def get_dashboard_stats(db: aiosqlite.Connection) -> dict:
    today = date.today().isoformat()

    cursor = await db.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'in' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS income_today,
            COALESCE(SUM(CASE WHEN direction = 'out' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS expense_today,
            COUNT(*) AS total_transactions,
            SUM(CASE WHEN category IS NULL OR category = 'Uncategorized' THEN 1 ELSE 0 END) AS uncategorized
        FROM transactions
        WHERE parse_status != 'ignored'
        """,
        (today, today),
    )
    row = await cursor.fetchone()
    await cursor.close()

    cursor = await db.execute(
        "SELECT COUNT(*) AS n FROM unknown_patterns WHERE status = 'pending'"
    )
    unknown_row = await cursor.fetchone()
    await cursor.close()

    last_sync = await get_last_sync(db)

    return {
        "income_today": row["income_today"],
        "expense_today": row["expense_today"],
        "total_transactions": row["total_transactions"],
        "uncategorized": row["uncategorized"] or 0,
        "unknown_parser": unknown_row["n"],
        "last_sync": last_sync,
    }


async def get_expense_by_day(db: aiosqlite.Connection, days: int = 7) -> list[dict]:
    """Return daily expense totals for the last `days`, including zero-total days."""
    days = days if days in (7, 14, 30) else 7
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)

    cursor = await db.execute(
        """
        SELECT date(occurred_at) AS day, COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND date(occurred_at) BETWEEN ? AND ?
        GROUP BY date(occurred_at)
        """,
        (start_day.isoformat(), end_day.isoformat()),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    totals_by_day = {row["day"]: float(row["total"] or 0) for row in rows}
    return [
        {
            "day": (start_day + timedelta(days=offset)).isoformat(),
            "total": totals_by_day.get((start_day + timedelta(days=offset)).isoformat(), 0.0),
        }
        for offset in range(days)
    ]


async def get_daily_summary_data(db: aiosqlite.Connection, day: str | None = None) -> dict:
    """Aggregate today's (or `day`'s) transactions grouped by category, for the LINE daily summary."""
    day = day or date.today().isoformat()

    cursor = await db.execute(
        """
        SELECT direction, category, amount FROM transactions
        WHERE date(occurred_at) = ? AND parse_status != 'ignored'
        """,
        (day,),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    income_total = 0.0
    income_count = 0
    expense_by_category: dict[str, float] = {}
    uncategorized_count = 0

    for r in rows:
        if r["direction"] == "in":
            income_total += r["amount"]
            income_count += 1
        elif r["direction"] == "out":
            category = r["category"] or "Uncategorized"
            if category == "Uncategorized":
                uncategorized_count += 1
            expense_by_category[category] = expense_by_category.get(category, 0.0) + r["amount"]

    cursor = await db.execute(
        "SELECT COUNT(*) AS n FROM unknown_patterns WHERE date(created_at) = ? AND status = 'pending'",
        (day,),
    )
    parse_error_row = await cursor.fetchone()
    await cursor.close()

    last_sync = await get_last_sync(db)

    return {
        "date": day,
        "income_total": income_total,
        "income_count": income_count,
        "expense_by_category": expense_by_category,
        "uncategorized_count": uncategorized_count,
        "parse_error_count": parse_error_row["n"],
        "last_sync": last_sync,
    }
