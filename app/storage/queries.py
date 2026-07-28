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


def _add_owner_filter(where: list[str], params: list, owner_user_id: int | None) -> None:
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)


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
    owner_user_id: int | None = None,
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
    _add_owner_filter(where, params, owner_user_id)

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


async def get_transaction(
    db: aiosqlite.Connection, transaction_id: int, owner_user_id: int | None = None
) -> dict | None:
    where = ["id = ?"]
    params: list = [transaction_id]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT * FROM transactions WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_transaction(row) if row else None


async def update_transaction_category(
    db: aiosqlite.Connection,
    transaction_id: int,
    category: str,
    category_source: str = "manual",
    owner_user_id: int | None = None,
) -> None:
    where = ["id = ?"]
    params: list = [category, category_source, transaction_id]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)
    await db.execute(
        f"""
        UPDATE transactions
        SET category = ?, category_source = ?, updated_at = CURRENT_TIMESTAMP
        WHERE {' AND '.join(where)}
        """,
        params,
    )
    await db.commit()


async def set_transaction_ignored(
    db: aiosqlite.Connection, transaction_id: int, ignored: bool, owner_user_id: int | None = None
) -> None:
    parse_status = "ignored" if ignored else "complete"
    where = ["id = ?"]
    params: list = [parse_status, transaction_id]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)
    await db.execute(
        f"UPDATE transactions SET parse_status = ?, updated_at = CURRENT_TIMESTAMP WHERE {' AND '.join(where)}",
        params,
    )
    await db.commit()


async def delete_transaction(
    db: aiosqlite.Connection, transaction_id: int, owner_user_id: int | None = None
) -> None:
    where = ["id = ?"]
    params: list = [transaction_id]
    _add_owner_filter(where, params, owner_user_id)
    await db.execute(f"DELETE FROM transactions WHERE {' AND '.join(where)}", params)
    await db.commit()


async def get_transaction_id_by_gmail_message_id(
    db: aiosqlite.Connection, gmail_message_id: str, owner_user_id: int | None = None
) -> int | None:
    where = ["gmail_message_id = ?"]
    params: list = [gmail_message_id]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT id FROM transactions WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return row["id"] if row else None


# ---- Unknown patterns -------------------------------------------------------

async def list_unknown(
    db: aiosqlite.Connection,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    owner_user_id: int | None = None,
    status: str | None = None,
) -> tuple[list[dict], int]:
    page = max(1, page)
    page_size = _clamp_page_size(page_size)

    where = []
    params: list = []
    _add_owner_filter(where, params, owner_user_id)
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


async def get_unknown(db: aiosqlite.Connection, unknown_id: int, owner_user_id: int | None = None) -> dict | None:
    where = ["id = ?"]
    params: list = [unknown_id]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT * FROM unknown_patterns WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_unknown(row) if row else None


async def set_unknown_status(
    db: aiosqlite.Connection, unknown_id: int, status: str, owner_user_id: int | None = None
) -> None:
    where = ["id = ?"]
    params: list = [status, unknown_id]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)
    await db.execute(f"UPDATE unknown_patterns SET status = ? WHERE {' AND '.join(where)}", params)
    await db.commit()


async def delete_unknown(db: aiosqlite.Connection, unknown_id: int, owner_user_id: int | None = None) -> None:
    where = ["id = ?"]
    params: list = [unknown_id]
    _add_owner_filter(where, params, owner_user_id)
    await db.execute(f"DELETE FROM unknown_patterns WHERE {' AND '.join(where)}", params)
    await db.commit()


# ---- Counterparty mappings ---------------------------------------------------

DATA_TABLES = (
    "transactions",
    "unknown_patterns",
    "ingestion_runs",
    "ingestion_state",
    "counterparty_mapping",
    "ignored_subjects",
)


def _row_to_user(row: aiosqlite.Row) -> dict:
    data = dict(row)
    data["is_active"] = bool(data["is_active"])
    return data


def _public_user(row: aiosqlite.Row | dict) -> dict:
    data = dict(row)
    data.pop("password_hash", None)
    data["is_active"] = bool(data["is_active"])
    return data


# ---- Users -----------------------------------------------------------------

async def count_users(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) AS n FROM users")
    row = await cursor.fetchone()
    await cursor.close()
    return row["n"]


async def count_active_admins(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1")
    row = await cursor.fetchone()
    await cursor.close()
    return row["n"]


async def get_default_owner_user_id(db: aiosqlite.Connection) -> int | None:
    cursor = await db.execute(
        """
        SELECT id FROM users
        WHERE role = 'admin' AND is_active = 1
        ORDER BY id ASC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row["id"] if row else None


async def claim_unowned_runtime_data(db: aiosqlite.Connection, owner_user_id: int) -> None:
    for table in DATA_TABLES:
        await db.execute(f"UPDATE {table} SET owner_user_id = ? WHERE owner_user_id IS NULL", (owner_user_id,))
    await db.commit()


async def list_users(db: aiosqlite.Connection) -> list[dict]:
    cursor = await db.execute(
        """
        SELECT id, email, display_name, role, is_active, created_at, updated_at
        FROM users
        ORDER BY role = 'admin' DESC, email ASC
        """
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [_public_user(row) for row in rows]


async def get_user(db: aiosqlite.Connection, user_id: int) -> dict | None:
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_user(row) if row else None


async def get_user_by_email(db: aiosqlite.Connection, email: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email.strip(),))
    row = await cursor.fetchone()
    await cursor.close()
    return _row_to_user(row) if row else None


async def create_user(
    db: aiosqlite.Connection,
    email: str,
    display_name: str,
    password_hash: str,
    role: str = "user",
    is_active: bool = True,
) -> dict:
    email = email.strip().lower()
    display_name = display_name.strip() or email
    role = role if role in ("admin", "user") else "user"
    cursor = await db.execute(
        """
        INSERT INTO users (email, display_name, password_hash, role, is_active)
        VALUES (?, ?, ?, ?, ?)
        """,
        (email, display_name, password_hash, role, int(is_active)),
    )
    await db.commit()
    user = await get_user(db, cursor.lastrowid)
    return _public_user(user)


async def update_user(
    db: aiosqlite.Connection,
    user_id: int,
    display_name: str,
    role: str,
    is_active: bool,
) -> dict | None:
    await db.execute(
        """
        UPDATE users
        SET display_name = ?, role = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (display_name.strip(), role, int(is_active), user_id),
    )
    await db.commit()
    user = await get_user(db, user_id)
    return _public_user(user) if user else None


async def update_user_password(db: aiosqlite.Connection, user_id: int, password_hash: str) -> None:
    await db.execute(
        "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (password_hash, user_id),
    )
    await db.commit()


async def list_mappings(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[dict]:
    where: list[str] = []
    params: list = []
    _add_owner_filter(where, params, owner_user_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cursor = await db.execute(f"SELECT * FROM counterparty_mapping {where_sql} ORDER BY counterparty ASC", params)
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows]


async def list_counterparty_options(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[dict]:
    """Return counterparties seen in transactions, newest/count-rich first."""
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    params = [owner_user_id] if owner_user_id is not None else []
    cursor = await db.execute(
        f"""
        SELECT
            counterparty,
            COALESCE(category, 'Uncategorized') AS category,
            COUNT(*) AS transaction_count,
            MAX(occurred_at) AS last_seen
        FROM transactions
        WHERE counterparty IS NOT NULL
          AND counterparty != ''
          AND counterparty != 'Unknown Counterparty'
          AND parse_status != 'ignored'
          {owner_sql}
        GROUP BY counterparty, COALESCE(category, 'Uncategorized')
        ORDER BY transaction_count DESC, last_seen DESC, counterparty ASC
        """,
        params,
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows]


async def list_category_options(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[str]:
    """Return categories from transactions and mappings, sorted for datalist use."""
    owner_tx_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_map_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    params = [owner_user_id, owner_user_id] if owner_user_id is not None else []
    cursor = await db.execute(
        f"""
        SELECT category FROM transactions
        WHERE category IS NOT NULL AND category != ''
        {owner_tx_sql}
        UNION
        SELECT category FROM counterparty_mapping
        WHERE category IS NOT NULL AND category != ''
        {owner_map_sql}
        ORDER BY category ASC
        """,
        params,
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [r["category"] for r in rows]


async def get_mapping(db: aiosqlite.Connection, mapping_id: int, owner_user_id: int | None = None) -> dict | None:
    where = ["id = ?"]
    params: list = [mapping_id]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT * FROM counterparty_mapping WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def create_mapping(
    db: aiosqlite.Connection,
    counterparty: str,
    category: str,
    source: str = "manual",
    owner_user_id: int | None = None,
) -> dict:
    cursor = await db.execute(
        """
        INSERT INTO counterparty_mapping (owner_user_id, counterparty, category, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(owner_user_id, counterparty) DO UPDATE SET
            category = excluded.category,
            source = excluded.source
        """,
        (owner_user_id, counterparty, category, source),
    )
    await db.commit()
    mapping_id = cursor.lastrowid
    row = await get_mapping(db, mapping_id, owner_user_id=owner_user_id)
    if row:
        return row
    # Conflict path: lastrowid is unreliable on UPSERT, look up by counterparty instead.
    if owner_user_id is None:
        cursor = await db.execute(
            "SELECT * FROM counterparty_mapping WHERE counterparty = ? ORDER BY id DESC LIMIT 1",
            (counterparty,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM counterparty_mapping WHERE owner_user_id = ? AND counterparty = ?",
            (owner_user_id, counterparty),
        )
    fetched = await cursor.fetchone()
    await cursor.close()
    return dict(fetched)


async def update_mapping(
    db: aiosqlite.Connection, mapping_id: int, category: str, owner_user_id: int | None = None
) -> None:
    where = ["id = ?"]
    params: list = [category, mapping_id]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(owner_user_id)
    await db.execute(f"UPDATE counterparty_mapping SET category = ? WHERE {' AND '.join(where)}", params)
    await db.commit()


async def delete_mapping(db: aiosqlite.Connection, mapping_id: int, owner_user_id: int | None = None) -> None:
    where = ["id = ?"]
    params: list = [mapping_id]
    _add_owner_filter(where, params, owner_user_id)
    await db.execute(f"DELETE FROM counterparty_mapping WHERE {' AND '.join(where)}", params)
    await db.commit()


# ---- Ignored subjects ---------------------------------------------------------

async def list_ignored_subjects(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[dict]:
    where: list[str] = []
    params: list = []
    _add_owner_filter(where, params, owner_user_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cursor = await db.execute(f"SELECT * FROM ignored_subjects {where_sql} ORDER BY created_at DESC, id DESC", params)
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows]


async def create_ignored_subject(
    db: aiosqlite.Connection,
    subject: str,
    reason: str | None = None,
    owner_user_id: int | None = None,
) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("subject is required")
    await db.execute(
        """
        INSERT INTO ignored_subjects (owner_user_id, subject, reason)
        VALUES (?, ?, ?)
        ON CONFLICT(owner_user_id, subject) DO UPDATE SET
            reason = COALESCE(excluded.reason, ignored_subjects.reason)
        """,
        (owner_user_id, subject, reason),
    )
    await db.commit()
    if owner_user_id is None:
        cursor = await db.execute(
            "SELECT * FROM ignored_subjects WHERE subject = ? ORDER BY id DESC LIMIT 1",
            (subject,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM ignored_subjects WHERE owner_user_id = ? AND subject = ?",
            (owner_user_id, subject),
        )
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row)


async def delete_ignored_subject(
    db: aiosqlite.Connection, ignored_subject_id: int, owner_user_id: int | None = None
) -> None:
    where = ["id = ?"]
    params: list = [ignored_subject_id]
    _add_owner_filter(where, params, owner_user_id)
    await db.execute(f"DELETE FROM ignored_subjects WHERE {' AND '.join(where)}", params)
    await db.commit()


async def is_subject_ignored(
    db: aiosqlite.Connection, subject: str | None, owner_user_id: int | None = None
) -> bool:
    if not subject:
        return False
    where = ["subject = ?"]
    params: list = [subject]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT 1 FROM ignored_subjects WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def mark_unknown_subject_ignored(
    db: aiosqlite.Connection, subject: str, owner_user_id: int | None = None
) -> int:
    where = ["subject = ?"]
    params: list = [subject]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"UPDATE unknown_patterns SET status = 'ignored' WHERE {' AND '.join(where)}", params)
    await db.commit()
    return cursor.rowcount


def _escape_gmail_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def apply_ignored_subjects_to_gmail_query(
    db: aiosqlite.Connection, query: str, owner_user_id: int | None = None
) -> str:
    ignored_subjects = await list_ignored_subjects(db, owner_user_id=owner_user_id)
    exclusions = [
        f'-subject:"{_escape_gmail_query_string(item["subject"])}"'
        for item in ignored_subjects
        if item.get("subject")
    ]
    return " ".join([query, *exclusions]).strip() if exclusions else query


# ---- Data management ----------------------------------------------------------

async def clear_runtime_data(db: aiosqlite.Connection, owner_user_id: int | None = None) -> dict:
    """Delete all user/runtime data while keeping schema and configuration files."""
    counts: dict[str, int] = {}
    await db.execute("PRAGMA foreign_keys = OFF")
    for table in DATA_TABLES:
        if owner_user_id is None:
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        else:
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE owner_user_id = ?", (owner_user_id,))
        counts[table] = (await cursor.fetchone())["n"]
        await cursor.close()
        if owner_user_id is None:
            await db.execute(f"DELETE FROM {table}")
        else:
            await db.execute(f"DELETE FROM {table} WHERE owner_user_id = ?", (owner_user_id,))
    if owner_user_id is None:
        placeholders = ",".join("?" for _ in DATA_TABLES)
        await db.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", DATA_TABLES)
    await db.commit()
    return counts


async def export_runtime_data(db: aiosqlite.Connection, owner_user_id: int | None = None) -> dict:
    """Export runtime tables to a JSON-serializable structure."""
    data = {"version": 1, "tables": {}}
    for table in DATA_TABLES:
        if owner_user_id is None:
            cursor = await db.execute(f"SELECT * FROM {table}")
        else:
            cursor = await db.execute(f"SELECT * FROM {table} WHERE owner_user_id = ?", (owner_user_id,))
        rows = await cursor.fetchall()
        await cursor.close()
        data["tables"][table] = [dict(row) for row in rows]
    return data


async def import_runtime_data(
    db: aiosqlite.Connection,
    payload: dict,
    replace: bool = True,
    owner_user_id: int | None = None,
) -> dict:
    """Import data created by `export_runtime_data`."""
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise ValueError("Invalid import payload: missing tables")

    imported: dict[str, int] = {}
    await db.execute("PRAGMA foreign_keys = OFF")
    if replace:
        for table in DATA_TABLES:
            if owner_user_id is None:
                await db.execute(f"DELETE FROM {table}")
            else:
                await db.execute(f"DELETE FROM {table} WHERE owner_user_id = ?", (owner_user_id,))

    for table in DATA_TABLES:
        rows = tables.get(table, [])
        if not rows:
            imported[table] = 0
            continue
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid rows for table: {table}")

        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = [row["name"] for row in await cursor.fetchall()]
        await cursor.close()
        insertable_columns = [column for column in columns if any(column in row for row in rows)]
        if owner_user_id is not None and "id" in insertable_columns:
            insertable_columns.remove("id")
        if owner_user_id is not None and "owner_user_id" in columns and "owner_user_id" not in insertable_columns:
            insertable_columns.append("owner_user_id")
        placeholders = ", ".join("?" for _ in insertable_columns)
        column_sql = ", ".join(insertable_columns)
        values = [
            tuple(owner_user_id if column == "owner_user_id" and owner_user_id is not None else row.get(column) for column in insertable_columns)
            for row in rows
        ]
        await db.executemany(
            f"INSERT OR REPLACE INTO {table} ({column_sql}) VALUES ({placeholders})",
            values,
        )
        imported[table] = len(values)

    await db.commit()
    return imported


# ---- Ingestion runs ----------------------------------------------------------

async def list_runs(
    db: aiosqlite.Connection,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    owner_user_id: int | None = None,
) -> tuple[list[dict], int]:
    page = max(1, page)
    page_size = _clamp_page_size(page_size)

    where: list[str] = []
    params: list = []
    _add_owner_filter(where, params, owner_user_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    count_cursor = await db.execute(f"SELECT COUNT(*) AS n FROM ingestion_runs {where_sql}", params)
    total = (await count_cursor.fetchone())["n"]
    await count_cursor.close()

    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"SELECT * FROM ingestion_runs {where_sql} ORDER BY run_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows], total


async def get_run(db: aiosqlite.Connection, run_id: int, owner_user_id: int | None = None) -> dict | None:
    where = ["id = ?"]
    params: list = [run_id]
    _add_owner_filter(where, params, owner_user_id)
    cursor = await db.execute(f"SELECT * FROM ingestion_runs WHERE {' AND '.join(where)}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def get_last_sync(db: aiosqlite.Connection, owner_user_id: int | None = None) -> str | None:
    where: list[str] = []
    params: list = []
    _add_owner_filter(where, params, owner_user_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cursor = await db.execute(f"SELECT MAX(run_at) AS last_run FROM ingestion_runs {where_sql}", params)
    row = await cursor.fetchone()
    await cursor.close()
    return row["last_run"] if row else None


# ---- Dashboard stats ----------------------------------------------------------

async def list_categories(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[str]:
    """Return distinct non-null categories from transactions, sorted."""
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    params = [owner_user_id] if owner_user_id is not None else []
    cursor = await db.execute(
        f"""
        SELECT DISTINCT category FROM transactions
        WHERE category IS NOT NULL AND category != ''
        {owner_sql}
        ORDER BY category ASC
        """,
        params,
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [r["category"] for r in rows]


async def list_transaction_types(db: aiosqlite.Connection, owner_user_id: int | None = None) -> list[str]:
    """Return distinct non-null transaction types from transactions, sorted."""
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    params = [owner_user_id] if owner_user_id is not None else []
    cursor = await db.execute(
        f"""
        SELECT DISTINCT transaction_type FROM transactions
        WHERE transaction_type IS NOT NULL AND transaction_type != ''
        {owner_sql}
        ORDER BY transaction_type ASC
        """,
        params,
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [r["transaction_type"] for r in rows]


async def get_dashboard_stats(db: aiosqlite.Connection, owner_user_id: int | None = None) -> dict:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'in' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS income_today,
            COALESCE(SUM(CASE WHEN direction = 'out' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS expense_today,
            COALESCE(SUM(CASE WHEN direction = 'in' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS income_yesterday,
            COALESCE(SUM(CASE WHEN direction = 'out' AND date(occurred_at) = ? THEN amount ELSE 0 END), 0) AS expense_yesterday,
            COUNT(*) AS total_transactions,
            SUM(CASE WHEN category IS NULL OR category = 'Uncategorized' THEN 1 ELSE 0 END) AS uncategorized
        FROM transactions
        WHERE parse_status != 'ignored'
        {owner_sql}
        """,
        [today, today, yesterday, yesterday, *owner_params],
    )
    row = await cursor.fetchone()
    await cursor.close()

    cursor = await db.execute(
        f"SELECT COUNT(*) AS n FROM unknown_patterns WHERE status = 'pending' {owner_sql}",
        owner_params,
    )
    unknown_row = await cursor.fetchone()
    await cursor.close()

    last_sync = await get_last_sync(db, owner_user_id=owner_user_id)

    return {
        "income_today": row["income_today"],
        "expense_today": row["expense_today"],
        "income_yesterday": row["income_yesterday"],
        "expense_yesterday": row["expense_yesterday"],
        "total_transactions": row["total_transactions"],
        "uncategorized": row["uncategorized"] or 0,
        "unknown_parser": unknown_row["n"],
        "last_sync": last_sync,
    }


async def get_expense_by_day(
    db: aiosqlite.Connection, days: int = 7, owner_user_id: int | None = None
) -> list[dict]:
    """Return daily expense totals for the last `days`, including zero-total days."""
    days = days if days in (7, 14, 30) else 7
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)

    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT date(occurred_at) AS day, COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND date(occurred_at) BETWEEN ? AND ?
          {owner_sql}
        GROUP BY date(occurred_at)
        """,
        [start_day.isoformat(), end_day.isoformat(), *owner_params],
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


async def get_expense_summary_windows(
    db: aiosqlite.Connection,
    windows: tuple[int, ...] = (7, 14, 30),
    owner_user_id: int | None = None,
) -> list[dict]:
    """Return expense totals for rolling day windows, inclusive of today."""
    allowed_windows = tuple(window for window in windows if window in (7, 14, 30))
    end_day = date.today()
    summaries: list[dict] = []
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    for window in allowed_windows:
        start_day = end_day - timedelta(days=window - 1)
        cursor = await db.execute(
            f"""
            SELECT
                COALESCE(SUM(amount), 0) AS total,
                COUNT(*) AS count
            FROM transactions
            WHERE direction = 'out'
              AND parse_status != 'ignored'
              AND date(occurred_at) BETWEEN ? AND ?
              {owner_sql}
            """,
            [start_day.isoformat(), end_day.isoformat(), *owner_params],
        )
        row = await cursor.fetchone()
        await cursor.close()
        summaries.append(
            {
                "days": window,
                "start_day": start_day.isoformat(),
                "end_day": end_day.isoformat(),
                "total": float(row["total"] or 0),
                "count": row["count"] or 0,
            }
        )

    return summaries


async def get_expense_by_bank(
    db: aiosqlite.Connection, days: int = 7, owner_user_id: int | None = None
) -> list[dict]:
    """Return total 'out' spend per bank over the last `days`, NULL bank as 'Unknown'."""
    days = days if days in (7, 14, 30) else 7
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT COALESCE(bank, 'Unknown') AS bank, COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND date(occurred_at) BETWEEN ? AND ?
          {owner_sql}
        GROUP BY COALESCE(bank, 'Unknown')
        ORDER BY total DESC
        """,
        [start_day.isoformat(), end_day.isoformat(), *owner_params],
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [{"bank": r["bank"], "total": float(r["total"] or 0)} for r in rows]


BANK_COLORS = {
    "KBank": "#2a78d6",
    "Krungsri": "#eb6834",
    "LH Bank": "#1baf7a",
    "SCB": "#eda100",
    "Unknown": "#A3A3A3",
}
# Fixed ring order for donut slices. Slice adjacency is deterministic (NOT
# magnitude-ordered) so the categorical palette's colorblind-safety - validated
# for exactly this adjacency - holds regardless of each bank's share.
CANONICAL_BANK_ORDER = ["KBank", "Krungsri", "LH Bank", "SCB", "Unknown"]
_DONUT_CIRCUMFERENCE = 100.0  # SVG circle r chosen (15.9155) so circumference ~= 100


def build_pie_segments(rows: list[dict]) -> list[dict]:
    """Turn get_expense_by_bank rows into SVG donut stroke-dasharray/dashoffset
    segments, emitted in fixed CANONICAL_BANK_ORDER so donut-slice adjacency is
    deterministic (a validated colorblind-safe ordering)."""
    def _order_key(row):
        try:
            return CANONICAL_BANK_ORDER.index(row["bank"])
        except ValueError:
            return len(CANONICAL_BANK_ORDER)

    ordered = sorted(rows, key=_order_key)
    total = sum(r["total"] for r in ordered)
    segments = []
    offset = 0.0
    for r in ordered:
        pct = (r["total"] / total * 100) if total else 0.0
        segments.append({
            "bank": r["bank"],
            "total": r["total"],
            "pct": pct,
            "color": BANK_COLORS.get(r["bank"], "#A3A3A3"),
            "dasharray": f"{pct:.4f} {_DONUT_CIRCUMFERENCE - pct:.4f}",
            "dashoffset": f"{-offset:.4f}",
        })
        offset += pct
    return segments


async def get_expense_by_category(
    db: aiosqlite.Connection, days: int = 30, owner_user_id: int | None = None, limit: int = 8
) -> list[dict]:
    """Return top expense categories by total 'out' spend over the last `days`."""
    days = days if days in (7, 14, 30) else 30
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT
            COALESCE(NULLIF(category, ''), 'Uncategorized') AS category,
            COALESCE(SUM(amount), 0) AS total,
            COUNT(*) AS count
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND date(occurred_at) BETWEEN ? AND ?
          {owner_sql}
        GROUP BY COALESCE(NULLIF(category, ''), 'Uncategorized')
        ORDER BY total DESC
        LIMIT ?
        """,
        [start_day.isoformat(), end_day.isoformat(), *owner_params, limit],
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [{"category": r["category"], "total": float(r["total"] or 0), "count": r["count"]} for r in rows]


async def get_top_counterparties(
    db: aiosqlite.Connection, days: int = 30, owner_user_id: int | None = None, limit: int = 5
) -> list[dict]:
    """Return top counterparties by total 'out' spend over the last `days`."""
    days = days if days in (7, 14, 30) else 30
    end_day = date.today()
    start_day = end_day - timedelta(days=days - 1)
    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT
            counterparty,
            COALESCE(SUM(amount), 0) AS total,
            COUNT(*) AS count
        FROM transactions
        WHERE direction = 'out'
          AND parse_status != 'ignored'
          AND counterparty IS NOT NULL AND counterparty != ''
          AND date(occurred_at) BETWEEN ? AND ?
          {owner_sql}
        GROUP BY counterparty
        ORDER BY total DESC
        LIMIT ?
        """,
        [start_day.isoformat(), end_day.isoformat(), *owner_params, limit],
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [{"counterparty": r["counterparty"], "total": float(r["total"] or 0), "count": r["count"]} for r in rows]


async def get_daily_summary_data(
    db: aiosqlite.Connection, day: str | None = None, owner_user_id: int | None = None
) -> dict:
    """Aggregate today's (or `day`'s) transactions grouped by category, for the LINE daily summary."""
    day = day or date.today().isoformat()

    owner_sql = "AND owner_user_id = ?" if owner_user_id is not None else ""
    owner_params = [owner_user_id] if owner_user_id is not None else []

    cursor = await db.execute(
        f"""
        SELECT direction, category, amount FROM transactions
        WHERE date(occurred_at) = ? AND parse_status != 'ignored'
        {owner_sql}
        """,
        [day, *owner_params],
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
        f"""
        SELECT COUNT(*) AS n FROM unknown_patterns
        WHERE date(created_at) = ? AND status = 'pending'
        {owner_sql}
        """,
        [day, *owner_params],
    )
    parse_error_row = await cursor.fetchone()
    await cursor.close()

    last_sync = await get_last_sync(db, owner_user_id=owner_user_id)

    return {
        "date": day,
        "income_total": income_total,
        "income_count": income_count,
        "expense_by_category": expense_by_category,
        "uncategorized_count": uncategorized_count,
        "parse_error_count": parse_error_row["n"],
        "last_sync": last_sync,
    }
