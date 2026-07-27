"""Ingestion Service - read Gmail, parse transactions, dedup, and persist to SQLite."""

import json
import logging
import time

from app.gmail import EmailMessage
from app.gmail.reader import GmailReader
from app.parsers.base import Transaction
from app.parsers.registry import ParserRegistry
from app.storage.database import get_connection

logger = logging.getLogger(__name__)


async def run_ingestion(
    query: str,
    reader: GmailReader | None = None,
    registry: ParserRegistry | None = None,
) -> dict:
    """Read emails matching `query`, parse them, and persist new transactions.

    Returns a summary dict with counts: emails_checked, inserted, duplicates, failed.
    """
    started_at = time.monotonic()
    reader = reader or GmailReader()
    registry = registry or ParserRegistry()

    messages = reader.read(query)

    inserted = duplicates = failed = 0
    db = await get_connection()

    try:
        for message in messages:
            if await _already_ingested(db, message.gmail_message_id):
                logger.info(f"Skipping duplicate message {message.gmail_message_id}")
                duplicates += 1
                continue

            transaction = registry.parse(message.body_text, message.sender, subject=message.subject)

            if transaction is None or transaction.parse_status == "failed":
                await _insert_unknown(db, message, transaction)
                failed += 1
                continue

            await _insert_transaction(db, message, transaction)
            inserted += 1

        duration = time.monotonic() - started_at
        await _record_run(
            db,
            emails_checked=len(messages),
            inserted=inserted,
            duplicates=duplicates,
            failed=failed,
            duration_seconds=duration,
        )
        await db.commit()
    finally:
        await db.close()

    summary = {
        "emails_checked": len(messages),
        "inserted": inserted,
        "duplicates": duplicates,
        "failed": failed,
    }
    logger.info(f"Ingestion complete: {summary}")
    return summary


async def _already_ingested(db, gmail_message_id: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM transactions WHERE gmail_message_id = ?", (gmail_message_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def _insert_transaction(db, message: EmailMessage, transaction: Transaction) -> None:
    await db.execute(
        """
        INSERT INTO transactions (
            transaction_type, direction, status, occurred_at, amount, fee,
            available_balance, counterparty, parser_version, parse_status,
            parse_confidence, raw_fields_json, gmail_message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction.transaction_type,
            transaction.direction,
            transaction.status,
            transaction.occurred_at,
            transaction.amount,
            transaction.fee,
            transaction.available_balance,
            transaction.counterparty,
            "1.0",
            transaction.parse_status,
            transaction.parse_confidence,
            json.dumps(transaction.raw_fields, ensure_ascii=False),
            message.gmail_message_id,
        ),
    )
    logger.info(f"Inserted transaction for message {message.gmail_message_id}")


async def _insert_unknown(db, message: EmailMessage, transaction: Transaction | None) -> None:
    raw_fields = transaction.raw_fields if transaction else {}
    await db.execute(
        """
        INSERT OR IGNORE INTO unknown_patterns (subject, sender, raw_fields_json, gmail_message_id)
        VALUES (?, ?, ?, ?)
        """,
        (message.subject, message.sender, json.dumps(raw_fields, ensure_ascii=False), message.gmail_message_id),
    )
    logger.warning(f"Could not parse message {message.gmail_message_id} ({message.subject!r}); logged as unknown")


async def _record_run(
    db, emails_checked: int, inserted: int, duplicates: int, failed: int, duration_seconds: float
) -> None:
    await db.execute(
        """
        INSERT INTO ingestion_runs (emails_checked, inserted, duplicates, failed, duration_seconds)
        VALUES (?, ?, ?, ?, ?)
        """,
        (emails_checked, inserted, duplicates, failed, duration_seconds),
    )
