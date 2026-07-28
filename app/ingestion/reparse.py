"""Reparse flow - refetch a single email from Gmail and re-run the parser against it.

Used by the "reparse" actions on the transaction detail and unknown-pattern views,
e.g. after a parser fix ships and a previously-failed email should be retried.
"""

import json
import logging

from app.classification.engine import CategoryEngine
from app.gmail import EmailMessage
from app.gmail.client import GmailClient
from app.ingestion import persistence
from app.parsers.base import Transaction
from app.parsers.registry import ParserRegistry
from app.storage import queries

logger = logging.getLogger(__name__)


async def _fetch_and_parse(
    gmail_message_id: str, gmail_client: GmailClient, registry: ParserRegistry
) -> tuple[EmailMessage, Transaction | None]:
    message = gmail_client.get_message(gmail_message_id)
    transaction = registry.parse(message.body_text, message.sender, subject=message.subject)
    return message, transaction


async def reparse_transaction(
    db,
    transaction_id: int,
    gmail_client: GmailClient | None = None,
    registry: ParserRegistry | None = None,
    engine: CategoryEngine | None = None,
) -> dict:
    """Re-run the parser for an existing transaction row, updating it in place."""
    row = await queries.get_transaction(db, transaction_id)
    if row is None:
        return {"status": "not_found"}

    gmail_client = gmail_client or GmailClient()
    registry = registry or ParserRegistry()
    engine = engine or CategoryEngine()

    message, transaction = await _fetch_and_parse(row["gmail_message_id"], gmail_client, registry)

    if transaction is not None and transaction.parse_status == "ignored":
        await db.execute(
            "UPDATE transactions SET parse_status = 'ignored', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (transaction_id,),
        )
        await db.commit()
        logger.info(f"Reparse of transaction {transaction_id} returned ignored")
        return {"status": "ignored"}

    if transaction is None or transaction.parse_status == "failed":
        warnings = transaction.parse_warnings if transaction else ["Parser returned no transaction"]
        await db.execute(
            "UPDATE transactions SET parse_status = 'failed', warnings_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(warnings, ensure_ascii=False), transaction_id),
        )
        await db.commit()
        logger.warning(f"Reparse of transaction {transaction_id} still failed: {warnings}")
        return {"status": "failed", "warnings": warnings}

    manual_override = row["category"] if row["category_source"] == "manual" else None
    category, category_source = await engine.categorize(
        db, persistence.transaction_to_dict(transaction), manual_override=manual_override
    )

    await db.execute(
        """
        UPDATE transactions SET
            transaction_type = ?, direction = ?, status = ?, occurred_at = ?, amount = ?, fee = ?,
            available_balance = ?, counterparty = ?, description = ?, category = ?, category_source = ?,
            bank = ?, parse_status = ?, parse_confidence = ?, warnings_json = ?, raw_fields_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
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
            transaction.description,
            category,
            category_source,
            registry.identify_bank(message.sender),
            transaction.parse_status,
            transaction.parse_confidence,
            json.dumps(transaction.parse_warnings, ensure_ascii=False),
            json.dumps(transaction.raw_fields, ensure_ascii=False),
            transaction_id,
        ),
    )
    await db.commit()
    logger.info(f"Reparsed transaction {transaction_id} successfully")
    return {"status": "parsed", "transaction_id": transaction_id}


async def reparse_unknown(
    db,
    unknown_id: int,
    gmail_client: GmailClient | None = None,
    registry: ParserRegistry | None = None,
    engine: CategoryEngine | None = None,
) -> dict:
    """Re-run the parser for an unknown-pattern row. Promotes it to `transactions` on success."""
    row = await queries.get_unknown(db, unknown_id)
    if row is None:
        return {"status": "not_found"}

    gmail_client = gmail_client or GmailClient()
    registry = registry or ParserRegistry()
    engine = engine or CategoryEngine()

    message, transaction = await _fetch_and_parse(row["gmail_message_id"], gmail_client, registry)

    if transaction is not None and transaction.parse_status == "ignored":
        await db.execute(
            """
            UPDATE unknown_patterns
            SET warnings_json = ?, raw_fields_json = ?, amount = ?, transaction_code = ?, status = 'ignored'
            WHERE id = ?
            """,
            (
                json.dumps(transaction.parse_warnings, ensure_ascii=False),
                json.dumps(transaction.raw_fields, ensure_ascii=False),
                transaction.amount,
                persistence.extract_transaction_code(transaction.raw_fields),
                unknown_id,
            ),
        )
        await db.commit()
        logger.info(f"Reparse of unknown pattern {unknown_id} returned ignored")
        return {"status": "ignored"}

    if transaction is None or transaction.parse_status == "failed":
        warnings = transaction.parse_warnings if transaction else ["Parser returned no transaction"]
        raw_fields = transaction.raw_fields if transaction else {}
        await db.execute(
            """
            UPDATE unknown_patterns
            SET warnings_json = ?, raw_fields_json = ?, amount = ?, transaction_code = ?, status = 'pending'
            WHERE id = ?
            """,
            (
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(raw_fields, ensure_ascii=False),
                transaction.amount if transaction else None,
                persistence.extract_transaction_code(raw_fields),
                unknown_id,
            ),
        )
        await db.commit()
        logger.warning(f"Reparse of unknown pattern {unknown_id} still failed: {warnings}")
        return {"status": "failed", "warnings": warnings}

    if await persistence.already_ingested(db, row["gmail_message_id"]):
        logger.info(f"Reparse of unknown pattern {unknown_id} now succeeds but transaction already exists")
        transaction_id = await queries.get_transaction_id_by_gmail_message_id(db, row["gmail_message_id"])
    else:
        bank = registry.identify_bank(message.sender)
        category, category_source = await engine.categorize(db, persistence.transaction_to_dict(transaction))
        transaction_id = await persistence.insert_transaction(db, message, transaction, category, category_source, bank=bank)

    await persistence.resolve_unknown(db, unknown_id, transaction_id)
    await db.commit()
    logger.info(f"Reparsed unknown pattern {unknown_id} -> transaction {transaction_id}")
    return {"status": "parsed", "transaction_id": transaction_id}
