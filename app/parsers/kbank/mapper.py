"""KBank Canonical Mapper - alias raw fields and parse them into typed values."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from dateutil import parser as date_parser

from app.parsers.kbank.aliases import to_canonical

logger = logging.getLogger(__name__)

_AMOUNT_RE = re.compile(r"(?P<number>[-+]?[\d,]+\.?\d*)\s*(?P<suffix>[kKmM])?")
_AMOUNT_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000}

# Thai Buddhist-era month abbreviations seen in KBank emails, e.g. "26 ม.ค. 2568"
_THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
_THAI_DATE_RE = re.compile(
    r"(\d{1,2})\s*(" + "|".join(re.escape(m) for m in _THAI_MONTHS) + r")\s*(\d{4})"
)


@dataclass
class CanonicalFields:
    """Parsed, typed representation of the fields extracted from a KBank email."""

    transaction_date: str | None = None  # ISO date, e.g. "2025-01-26"
    transaction_time: str | None = None  # "HH:MM[:SS]"
    amount: float | None = None
    fee: float | None = None
    balance: float | None = None
    from_account: str | None = None
    to_account: str | None = None
    counterparty: str | None = None
    reference: str | None = None
    status: str | None = None
    channel: str | None = None
    raw_fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _parse_amount(raw: str) -> float | None:
    """Parse an amount, tolerating currency symbols/codes, commas, and "1.5k"-style shorthand."""
    match = _AMOUNT_RE.search(raw.replace(",", ""))
    if not match or not match.group("number"):
        return None
    try:
        value = float(match.group("number"))
    except ValueError:
        return None
    suffix = match.group("suffix")
    if suffix:
        value *= _AMOUNT_SUFFIX_MULTIPLIERS[suffix.lower()]
    return value


def _thai_year_to_gregorian(year: int) -> int:
    # KBank Thai dates use the Buddhist Era (BE = CE + 543)
    return year - 543 if year > 2400 else year


def _parse_thai_date(raw: str) -> str | None:
    match = _THAI_DATE_RE.search(raw)
    if not match:
        return None
    day, month_abbr, year = match.groups()
    month = _THAI_MONTHS[month_abbr]
    year = _thai_year_to_gregorian(int(year))
    try:
        return datetime(year, month, int(day)).date().isoformat()
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    thai = _parse_thai_date(raw)
    if thai:
        return thai
    try:
        return date_parser.parse(raw, dayfirst=True, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError):
        return None


def _parse_time(raw: str) -> str | None:
    match = re.search(r"\d{1,2}:\d{2}(:\d{2})?", raw)
    return match.group() if match else None


_FIELD_PARSERS = {
    "transaction_date": _parse_date,
    "transaction_time": _parse_time,
    "amount": _parse_amount,
    "fee": _parse_amount,
    "balance": _parse_amount,
}


def map_fields(raw_fields: list[tuple[str, str]]) -> CanonicalFields:
    """Alias raw (label, value) pairs to canonical fields and parse their types."""
    canonical = CanonicalFields()

    for raw_label, raw_value in raw_fields:
        canonical.raw_fields[raw_label] = raw_value

        field_name = to_canonical(raw_label)
        if field_name is None:
            continue

        parser_fn = _FIELD_PARSERS.get(field_name)
        parsed_value = parser_fn(raw_value) if parser_fn else raw_value

        if parsed_value is None:
            canonical.warnings.append(f"Could not parse '{field_name}' from value: {raw_value!r}")
            continue

        # Keep the first value seen - later duplicates (e.g. a bilingual repeat) are ignored.
        if getattr(canonical, field_name) is None:
            setattr(canonical, field_name, parsed_value)

    return canonical
