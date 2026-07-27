"""Shared DB write helpers for persisting parsed transactions/unknown emails.

Used by both the ingestion service (new emails) and the reparse flow (re-running
the parser against an already-seen message).
"""

import json
import logging
import re

from app.gmail import EmailMessage
from app.parsers.base import Transaction

logger = logging.getLogger(__name__)

_REFERENCE_KEY_RE = re.compile(r"ref|เลขที่", re.IGNORECASE)


async def already_ingested(db, gmail_message_id: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM transactions WHERE gmail_message_id = ?", (gmail_message_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def find_duplicate_transaction(db, transaction: Transaction) -> bool:
    """Detect the same transaction arriving under a different gmail_message_id
    (e.g. a bank resend/forward). Prefers the bank's own reference number when
    the parser found one; otherwise falls back to a fingerprint of
    (type, direction, amount, occurred_at, counterparty).
    """
    if transaction.transaction_id:
        cursor = await db.execute(
            "SELECT 1 FROM transactions WHERE transaction_id = ?", (transaction.transaction_id,)
        )
    else:
        cursor = await db.execute(
            """
            SELECT 1 FROM transactions
            WHERE transaction_type = ? AND direction = ? AND amount = ?
              AND occurred_at = ? AND counterparty IS ?
            """,
            (
                transaction.transaction_type,
                transaction.direction,
                transaction.amount,
                transaction.occurred_at,
                transaction.counterparty,
            ),
        )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def insert_transaction(
    db, message: EmailMessage, transaction: Transaction, category: str, category_source: str
) -> None:
    await db.execute(
        """
        INSERT INTO transactions (
            transaction_id, transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, description, category, category_source,
            parser_version, parse_status, parse_confidence, warnings_json,
            raw_fields_json, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction.transaction_id,
            transaction.transaction_type,
            transaction.direction,
            transaction.status,
            transaction.occurred_at,
            transaction.amount,
            transaction.fee,
            transaction.available_balance,
            transaction.counterparty,
            transaction.description,
            category,
            category_source,
            "1.0",
            transaction.parse_status,
            transaction.parse_confidence,
            json.dumps(transaction.parse_warnings, ensure_ascii=False),
            json.dumps(transaction.raw_fields, ensure_ascii=False),
            message.gmail_message_id,
        ),
    )
    logger.info(f"Inserted transaction for message {message.gmail_message_id}")


async def insert_unknown(db, message: EmailMessage, transaction: Transaction | None) -> None:
    raw_fields = transaction.raw_fields if transaction else {}
    amount = transaction.amount if transaction else None
    warnings = transaction.parse_warnings if transaction else []
    transaction_code = extract_transaction_code(raw_fields)

    await db.execute(
        """
        INSERT OR IGNORE INTO unknown_patterns (
            subject, sender, transaction_code, amount, warnings_json,
            raw_fields_json, parser_version, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.subject,
            message.sender,
            transaction_code,
            amount,
            json.dumps(warnings, ensure_ascii=False),
            json.dumps(raw_fields, ensure_ascii=False),
            "1.0",
            message.gmail_message_id,
        ),
    )
    logger.warning(f"Could not parse message {message.gmail_message_id} ({message.subject!r}); logged as unknown")


def extract_transaction_code(raw_fields: dict) -> str | None:
    """Best-effort pull of a reference/transaction-code value from raw label:value fields."""
    for label, value in raw_fields.items():
        if _REFERENCE_KEY_RE.search(label):
            return value
    return None


def transaction_to_dict(transaction: Transaction) -> dict:
    return {
        "transaction_type": transaction.transaction_type,
        "direction": transaction.direction,
        "counterparty": transaction.counterparty,
        "amount": transaction.amount,
    }


async def record_run(
    db, emails_checked: int, inserted: int, duplicates: int, failed: int, duration_seconds: float
) -> None:
    await db.execute(
        """
        INSERT INTO ingestion_runs (emails_checked, inserted, duplicates, failed, duration_seconds)
        VALUES (?, ?, ?, ?, ?)
        """,
        (emails_checked, inserted, duplicates, failed, duration_seconds),
    )
