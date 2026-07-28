"""LH Bank parser - full pipeline: normalize -> extract -> map -> validate."""

import logging

from app.parsers.base import BaseParser, Transaction
from app.parsers.lhbank.detector import is_lhbank_sender
from app.parsers.lhbank.extractor import extract_fields
from app.parsers.lhbank.mapper import CanonicalFields, map_fields
from app.parsers.lhbank.normalizer import normalize
from app.parsers.lhbank.validator import validate

logger = logging.getLogger(__name__)

# Transaction phrasing found in the notification prose (not a labelled field).
_TYPE_MAP = {
    "จ่ายบิล": "bill_payment",
}


def _detect_transaction_type(text: str) -> str:
    for phrase, transaction_type in _TYPE_MAP.items():
        if phrase in text:
            return transaction_type
    return "unknown"


def _build_description(canonical: CanonicalFields) -> str | None:
    """Combine the source account info and device into a human-readable note."""
    parts: list[str] = []
    if canonical.from_account_info:
        parts.append(canonical.from_account_info)
    if canonical.device:
        parts.append(f"({canonical.device})")
    return " ".join(parts) if parts else None


class LHBankParser(BaseParser):
    """LH Bank (Land and House Bank) email parser."""

    def can_handle(self, sender: str) -> bool:
        """Check if this is an LH Bank email."""
        return is_lhbank_sender(sender)

    def parse(self, email_text: str, subject: str = "") -> Transaction | None:
        """Parse an LH Bank notification email body into a canonical Transaction."""
        try:
            normalized = normalize(email_text)
            raw_fields = extract_fields(normalized)

            if not raw_fields:
                logger.warning("LHBankParser: no fields found in email")
                return None

            canonical = map_fields(raw_fields)
            parse_status, parse_confidence, warnings = validate(canonical)

            if parse_status == "failed":
                logger.warning(f"LHBankParser: failed to parse required fields: {warnings}")

            transaction_type = _detect_transaction_type(normalized)
            # These notifications report a completed bill payment ("จ่ายบิลสำเร็จ").
            status = "success" if "สำเร็จ" in normalized else "unknown"

            return Transaction(
                transaction_type=transaction_type,
                direction="out",
                status=status,
                occurred_at=canonical.occurred_at or "",
                amount=canonical.amount or 0.0,
                fee=canonical.fee or 0.0,
                available_balance=None,
                counterparty=canonical.counterparty or "Unknown Counterparty",
                description=_build_description(canonical) or subject or None,
                parse_status=parse_status,
                parse_confidence=parse_confidence,
                parse_warnings=warnings,
                raw_fields=canonical.raw_fields,
                transaction_id=canonical.reference_2,
            )
        except Exception:
            logger.exception("LHBankParser: unexpected error while parsing email")
            return None
