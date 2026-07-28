"""Krungsri Canonical Mapper - alias raw fields and parse them into typed values."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from app.parsers.krungsri.aliases import to_canonical

logger = logging.getLogger(__name__)

_AMOUNT_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_FOOTER_PREFIXES = (
    "หากท่านไม่ได้เป็นผู้ทำรายการ",
    "อีเมลฉบับนี้ เป็นการแจ้งข้อมูลโดยอัตโนมัติ",
)

# Krungsri timestamps look like "03/07/2569 19:21:13" (DD/MM/YYYY, Buddhist year).
_DATETIME_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)


@dataclass
class CanonicalFields:
    """Parsed, typed representation of the fields extracted from a Krungsri email."""

    result: str | None = None
    transaction_type: str | None = None
    account_name: str | None = None
    counterparty: str | None = None
    amount: float | None = None
    fee: float | None = None
    to_wallet: str | None = None
    merchant_code: str | None = None
    merchant_reference: str | None = None
    transaction_code: str | None = None
    reference_1: str | None = None
    reference_2: str | None = None
    reference_number: str | None = None
    occurred_at: str | None = None
    memo: str | None = None
    recipient_memo: str | None = None
    raw_fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _parse_amount(raw: str) -> float | None:
    """Parse a numeric amount, tolerating commas and currency text."""
    match = _AMOUNT_RE.search(raw.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _parse_occurred_at(raw: str) -> str | None:
    """Parse "DD/MM/YYYY HH:MM:SS" (Buddhist year) into an ISO datetime string."""
    match = _DATETIME_RE.search(raw)
    if not match:
        return None
    parts = match.groupdict()
    year = int(parts["year"])
    # Buddhist Era -> Gregorian (BE = CE + 543).
    year = year - 543 if year > 2400 else year
    try:
        dt = datetime(
            year,
            int(parts["month"]),
            int(parts["day"]),
            int(parts["hour"] or 0),
            int(parts["minute"] or 0),
            int(parts["second"] or 0),
        )
    except ValueError:
        return None
    return dt.isoformat()


def _clean_memo(raw: str) -> str:
    value = raw.strip()
    if any(value.startswith(prefix) for prefix in _FOOTER_PREFIXES):
        return ""
    return value


_FIELD_PARSERS = {
    "amount": _parse_amount,
    "fee": _parse_amount,
    "occurred_at": _parse_occurred_at,
    "memo": _clean_memo,
    "recipient_memo": _clean_memo,
}


def map_fields(raw_fields: list[tuple[str, str]]) -> CanonicalFields:
    """Alias raw (label, value) pairs to canonical fields and parse their types.

    Every field is preserved in `raw_fields`, keyed by canonical name when known
    and by the raw label otherwise, so nothing from the email is discarded.
    """
    canonical = CanonicalFields()

    for raw_label, raw_value in raw_fields:
        field_name = to_canonical(raw_label)
        key = field_name if field_name else raw_label
        canonical.raw_fields[key] = raw_value

        if field_name is None:
            continue

        parser_fn = _FIELD_PARSERS.get(field_name)
        if parser_fn:
            parsed_value = parser_fn(raw_value)
            if parsed_value is None and raw_value:
                canonical.warnings.append(
                    f"Could not parse '{field_name}' from value: {raw_value!r}"
                )
                continue
            if field_name in {"memo", "recipient_memo"}:
                canonical.raw_fields[key] = parsed_value
        else:
            parsed_value = raw_value

        if getattr(canonical, field_name) is None:
            setattr(canonical, field_name, parsed_value)

    # Memo is meaningful even when blank, so always expose it.
    canonical.raw_fields.setdefault("memo", canonical.memo or "")

    return canonical
