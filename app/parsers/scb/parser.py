"""SCB parser - full pipeline: normalize -> extract -> map -> validate."""

import logging

from app.parsers.base import BaseParser, Transaction
from app.parsers.scb.detector import is_scb_sender
from app.parsers.scb.extractor import extract_fields
from app.parsers.scb.mapper import map_fields
from app.parsers.scb.normalizer import normalize
from app.parsers.scb.validator import validate

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "โอนเงินไปธนาคารอื่น": "bank_transfer",
    "โอนเงิน": "bank_transfer",
    "ชำระค่าสินค้าและบริการ": "bill_payment",
    "ชำระบิล": "bill_payment",
}


class SCBParser(BaseParser):
    """SCB (Siam Commercial Bank) email parser."""

    def can_handle(self, sender: str) -> bool:
        """Check if this is an SCB email."""
        return is_scb_sender(sender)

    def parse(self, email_text: str, subject: str = "") -> Transaction | None:
        """Parse an SCB notification email body into a canonical Transaction."""
        try:
            normalized = normalize(email_text)
            raw_fields = extract_fields(normalized)

            if not raw_fields:
                logger.warning("SCBParser: no fields found in email")
                return None

            canonical = map_fields(raw_fields)
            parse_status, parse_confidence, warnings = validate(canonical)

            if parse_status == "failed":
                logger.warning(f"SCBParser: failed to parse required fields: {warnings}")

            transaction_type = _TYPE_MAP.get(
                (canonical.transaction_type_label or "").strip(), "unknown"
            )

            counterparty = None
            if canonical.to_bank or canonical.to_account:
                counterparty = " ".join(
                    part for part in (canonical.to_bank, canonical.to_account) if part
                )

            return Transaction(
                transaction_type=transaction_type,
                direction="out",
                status="success",
                occurred_at=canonical.occurred_at or "",
                amount=canonical.amount or 0.0,
                fee=0.0,
                available_balance=None,
                counterparty=counterparty or "Unknown Counterparty",
                description=canonical.details or subject or None,
                parse_status=parse_status,
                parse_confidence=parse_confidence,
                parse_warnings=warnings,
                raw_fields=canonical.raw_fields,
                transaction_id=None,
            )
        except Exception:
            logger.exception("SCBParser: unexpected error while parsing email")
            return None
