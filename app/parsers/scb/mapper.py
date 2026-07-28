"""SCB Canonical Mapper - alias raw fields and parse them into typed values."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

_AMOUNT_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")

# Thai month abbreviations -> Gregorian month number.
_THAI_MONTHS = {
    "ม.ค.": 1,
    "ก.พ.": 2,
    "มี.ค.": 3,
    "เม.ย.": 4,
    "พ.ค.": 5,
    "มิ.ย.": 6,
    "ก.ค.": 7,
    "ส.ค.": 8,
    "ก.ย.": 9,
    "ต.ค.": 10,
    "พ.ย.": 11,
    "ธ.ค.": 12,
}

_MONTH_ALT = "|".join(re.escape(m) for m in _THAI_MONTHS)

# SCB timestamps look like "28 ก.ค. 2569 ณ 07:16:35" (DD <Thai month> Buddhist year).
_DATETIME_RE = re.compile(
    rf"(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_ALT})\s+(?P<year>\d{{4}})"
    r"(?:\s+ณ\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)


@dataclass
class CanonicalFields:
    """Parsed, typed representation of the fields extracted from an SCB email."""

    transaction_type_label: str | None = None
    from_bank: str | None = None
    from_account: str | None = None
    to_bank: str | None = None
    to_account: str | None = None
    details: str | None = None
    amount: float | None = None
    occurred_at: str | None = None
    raw_fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _parse_amount(raw: str) -> float | None:
    """Parse a numeric amount, tolerating commas and the trailing "บาท" currency text."""
    match = _AMOUNT_RE.search(raw.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _parse_occurred_at(raw: str) -> str | None:
    """Parse "DD <Thai month> YYYY ณ HH:MM:SS" (Buddhist year) into an ISO datetime."""
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
            _THAI_MONTHS[parts["month"]],
            int(parts["day"]),
            int(parts["hour"] or 0),
            int(parts["minute"] or 0),
            int(parts["second"] or 0),
        )
    except (ValueError, KeyError):
        return None
    return dt.isoformat()


_FIELD_PARSERS = {
    "amount": _parse_amount,
    "occurred_at": _parse_occurred_at,
}


def map_fields(raw_fields: dict) -> CanonicalFields:
    """Alias raw fields to canonical fields and parse their types.

    Every field is preserved in `raw_fields` so nothing from the email is discarded.
    """
    canonical = CanonicalFields()

    for key, raw_value in raw_fields.items():
        canonical.raw_fields[key] = raw_value

        parser_fn = _FIELD_PARSERS.get(key)
        if parser_fn:
            parsed_value = parser_fn(raw_value)
            if parsed_value is None and raw_value:
                canonical.warnings.append(
                    f"Could not parse '{key}' from value: {raw_value!r}"
                )
                continue
        else:
            parsed_value = raw_value

        if hasattr(canonical, key) and getattr(canonical, key) is None:
            setattr(canonical, key, parsed_value)

    return canonical
