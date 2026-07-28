"""KBank parser - full pipeline: normalize -> detect -> extract -> map -> classify -> validate."""

import logging

from app.parsers.base import BaseParser, Transaction
from app.parsers.kbank.detector import detect_section
from app.parsers.kbank.extractor import extract_fields
from app.parsers.kbank.mapper import map_fields
from app.parsers.kbank.normalizer import normalize
from app.parsers.kbank.transaction_detector import detect as detect_transaction_attrs
from app.parsers.kbank.validator import validate

logger = logging.getLogger(__name__)

_IGNORED_SUBJECT_MARKERS = (
    "email statement",
)


def _is_ignored_subject(subject: str) -> bool:
    subject_lower = (subject or "").lower()
    return any(marker in subject_lower for marker in _IGNORED_SUBJECT_MARKERS)


class KBankParser(BaseParser):
    """KBank (Kasikorn Bank) email parser."""

    def can_handle(self, sender: str) -> bool:
        """Check if this is a KBank email."""
        return "kasikornbank" in sender.lower() or "kplus" in sender.lower()

    def parse(self, email_text: str, subject: str = "") -> Transaction | None:
        """Parse a KBank notification email body into a canonical Transaction."""
        try:
            if _is_ignored_subject(subject):
                logger.info("KBankParser: ignoring non-transaction notification")
                return Transaction(
                    transaction_type="notification",
                    direction="unknown",
                    status="ignored",
                    occurred_at="",
                    amount=0.0,
                    description=subject or None,
                    parse_status="ignored",
                    parse_confidence=1.0,
                    parse_warnings=[],
                    raw_fields={"ignored_reason": "non_transaction_notification"},
                )

            normalized = normalize(email_text)
            section = detect_section(normalized)
            raw_fields = extract_fields(section.section_text)

            if not raw_fields:
                logger.warning("KBankParser: no label:value fields found in email")
                return None

            canonical = map_fields(raw_fields)
            attrs = detect_transaction_attrs(subject, canonical, body_text=normalized)
            parse_status, parse_confidence, warnings = validate(canonical, attrs)

            if parse_status == "failed":
                logger.warning(f"KBankParser: failed to parse required fields: {warnings}")

            occurred_at = _combine_datetime(canonical.transaction_date, canonical.transaction_time)

            return Transaction(
                transaction_type=attrs.transaction_type,
                direction=attrs.direction,
                status=attrs.status,
                occurred_at=occurred_at or "",
                amount=canonical.amount or 0.0,
                fee=canonical.fee or 0.0,
                available_balance=canonical.balance,
                counterparty=canonical.counterparty or "Unknown Counterparty",
                description=subject or None,
                parse_status=parse_status,
                parse_confidence=parse_confidence,
                parse_warnings=warnings,
                raw_fields=canonical.raw_fields,
                transaction_id=canonical.reference,
            )
        except Exception:
            logger.exception("KBankParser: unexpected error while parsing email")
            return None


def _combine_datetime(date_str: str | None, time_str: str | None) -> str | None:
    if not date_str:
        return None
    if time_str:
        return f"{date_str}T{time_str}"
    return f"{date_str}T00:00:00"
