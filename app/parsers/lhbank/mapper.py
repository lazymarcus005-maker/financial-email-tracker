"""LH Bank Canonical Mapper - alias raw fields and parse them into typed values."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from app.parsers.lhbank.aliases import to_canonical

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

# LH Bank timestamps look like "วันอาทิตย์, 26 ก.ค. 2569 12:15" - a day-of-week
# prefix, then "day Thai-month Buddhist-year hour:minute". Anchoring on the
# numeric day naturally ignores the leading day-of-week text.
_DATETIME_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>\S+)\s+(?P<year>\d{4})"
    r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)


@dataclass
class CanonicalFields:
    """Parsed, typed representation of the fields extracted from an LH Bank email."""

    occurred_at: str | None = None
    device: str | None = None
    from_account_info: str | None = None
    counterparty: str | None = None
    merchant_code_1: str | None = None
    reference_2: str | None = None
    amount: float | None = None
    fee: float | None = None
    memo: str | None = None
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
    """Parse "วันอาทิตย์, 26 ก.ค. 2569 12:15" into an ISO datetime string.

    The day-of-week prefix is dropped, the Thai month abbreviation is mapped to a
    month number, and the Buddhist Era year is converted to Gregorian (BE - 543).
    """
    match = _DATETIME_RE.search(raw)
    if not match:
        return None
    parts = match.groupdict()
    month = _THAI_MONTHS.get(parts["month"])
    if month is None:
        return None
    year = int(parts["year"])
    # Buddhist Era -> Gregorian (BE = CE + 543).
    year = year - 543 if year > 2400 else year
    try:
        dt = datetime(
            year,
            month,
            int(parts["day"]),
            int(parts["hour"]),
            int(parts["minute"]),
        )
    except ValueError:
        return None
    return dt.isoformat()


def _clean_counterparty(raw: str) -> str:
    """Drop a truncated trailing parenthetical such as " (HEAD" (open, no close)."""
    return re.sub(r"\s*\([^)]*$", "", raw).strip()


_FIELD_PARSERS = {
    "amount": _parse_amount,
    "fee": _parse_amount,
    "occurred_at": _parse_occurred_at,
    "counterparty": _clean_counterparty,
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
        else:
            parsed_value = raw_value

        if getattr(canonical, field_name) is None:
            setattr(canonical, field_name, parsed_value)

    # Memo is meaningful even when blank, so always expose it.
    canonical.raw_fields.setdefault("memo", canonical.memo or "")

    return canonical
