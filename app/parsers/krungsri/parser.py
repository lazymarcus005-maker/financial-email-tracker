"""Krungsri parser - full pipeline: normalize -> extract -> map -> validate."""

import logging

from app.parsers.base import BaseParser, Transaction
from app.parsers.krungsri.detector import is_krungsri_sender
from app.parsers.krungsri.extractor import extract_fields
from app.parsers.krungsri.mapper import map_fields
from app.parsers.krungsri.normalizer import normalize
from app.parsers.krungsri.validator import validate

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    "ทำรายการสำเร็จ": "success",
    "สำเร็จ": "success",
    "ไม่สำเร็จ": "failed",
    "ล้มเหลว": "failed",
    "รอดำเนินการ": "pending",
    "ยกเลิก": "cancelled",
}

_TYPE_MAP = {
    "ชำระค่าสินค้าและบริการ": "bill_payment",
    "โอนเงิน": "bank_transfer",
    "โอนเงินพร้อมเพย์": "promptpay_transfer",
    "โอนเงินเข้าบัญชีบุคคลอื่นต่างธนาคาร": "bank_transfer",
    "โอนเงินไปยังบัญชีบุคคลอื่น": "bank_transfer",
    "โอนเงินไปยังพร้อมเพย์": "promptpay_transfer",
    "โอนเงิน/เติมเงินเข้า e-Wallet": "topup",
    "ชำระบิล": "bill_payment",
}


class KrungsriParser(BaseParser):
    """Krungsri (Bank of Ayudhya) email parser."""

    def can_handle(self, sender: str) -> bool:
        """Check if this is a Krungsri email."""
        return is_krungsri_sender(sender)

    def parse(self, email_text: str, subject: str = "") -> Transaction | None:
        """Parse a Krungsri notification email body into a canonical Transaction."""
        try:
            normalized = normalize(email_text)
            raw_fields = extract_fields(normalized)

            if not raw_fields:
                logger.warning("KrungsriParser: no label:value fields found in email")
                return None

            canonical = map_fields(raw_fields)
            parse_status, parse_confidence, warnings = validate(canonical)

            if parse_status == "failed":
                logger.warning(f"KrungsriParser: failed to parse required fields: {warnings}")

            status = _STATUS_MAP.get((canonical.result or "").strip(), "unknown")
            transaction_type = _TYPE_MAP.get((canonical.transaction_type or "").strip(), "unknown")

            return Transaction(
                transaction_type=transaction_type,
                direction="out",
                status=status,
                occurred_at=canonical.occurred_at or "",
                amount=canonical.amount or 0.0,
                fee=canonical.fee or 0.0,
                available_balance=None,
                counterparty=canonical.counterparty or "Unknown Counterparty",
                description=canonical.memo or subject or None,
                parse_status=parse_status,
                parse_confidence=parse_confidence,
                parse_warnings=warnings,
                raw_fields=canonical.raw_fields,
                transaction_id=canonical.reference_number,
            )
        except Exception:
            logger.exception("KrungsriParser: unexpected error while parsing email")
            return None
