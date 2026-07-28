"""LH Bank Field Extractor - pull fields out of a hybrid-format text block.

LH Bank notifications mix three layouts:

* Section headers on their own line ("รายละเอียด", "จาก", "ไปยัง"). Some double
  as field labels ("จาก"/"ไปยัง" carry multi-line values); pure headers like
  "รายละเอียด" carry no value and are skipped.
* A label on one line with its value on the next ("วันเวลา", "จำนวนเงิน (บาท)",
  "บันทึกช่วยจำ" whose value may be empty).
* "Label : Value" on a single line ("หมายเลขร้านค้า1 : 000002205808025").

A value may span multiple lines (the "จาก" account block continues onto the
account-type line) or be entirely empty (a blank memo).
"""

import re

from app.parsers.lhbank.aliases import CANONICAL_ALIASES

# Labels that introduce a field. "จาก"/"ไปยัง" are section headers that also own
# a (multi-line) value, so they belong here.
KNOWN_LABELS = set(CANONICAL_ALIASES.keys())

# Pure structural headers that carry no value of their own.
SECTION_HEADERS = {"รายละเอียด"}


def _split_inline_label(line: str) -> tuple[str, str] | None:
    """Split a "Label : Value" line, but only when the label is a known field.

    Value lines such as "XXX-X-15441-X : นาย พิชเยนทร์ เย็นศิริ" also contain a
    colon yet are not field labels, so they are left untouched (returns None).
    """
    match = re.match(r"^(?P<label>.+?)\s*[:：]\s*(?P<value>.*)$", line)
    if not match:
        return None
    label = match.group("label").strip()
    if label in KNOWN_LABELS:
        return label, match.group("value").strip()
    return None


def _is_boundary(line: str) -> bool:
    """Return True if `line` starts a new field/section (i.e. ends the current value)."""
    if line in KNOWN_LABELS or line in SECTION_HEADERS:
        return True
    return _split_inline_label(line) is not None


def extract_fields(text: str) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from the email body.

    A standalone label collects the following non-label lines as its value
    (joined with single spaces) until the next label/section header or EOF.
    Empty values (label immediately followed by another label or EOF) yield "".
    """
    fields: list[tuple[str, str]] = []
    lines = text.split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        inline = _split_inline_label(line)
        if inline is not None:
            fields.append(inline)
            i += 1
            continue

        if line in SECTION_HEADERS:
            i += 1
            continue

        if line in KNOWN_LABELS:
            label = line
            value_parts: list[str] = []
            i += 1
            while i < n:
                next_line = lines[i].strip()
                if next_line and _is_boundary(next_line):
                    break
                if next_line:
                    value_parts.append(next_line)
                i += 1
            fields.append((label, re.sub(r"\s+", " ", " ".join(value_parts)).strip()))
            continue

        # Greeting / prose line that is not a field label.
        i += 1

    return fields
