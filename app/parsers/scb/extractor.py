"""SCB Field Extractor - pull section-based fields out of a text block.

SCB notifications differ from KBank/Krungsri: there is no "Label: Value" layout.
Instead a section header sits on its own line (usually ending with a colon) and
its value follows on the next line(s). The "รายละเอียด" (details) section spans
multiple lines and embeds the from/to bank and account numbers, which are parsed
out into their own fields.
"""

import re

from app.parsers.scb.aliases import to_canonical

# Top-level section headers. "จำนวนเงิน" appears without a trailing colon in SCB
# emails, so headers are matched on their stripped label rather than a colon.
SECTION_HEADERS = {
    "ประเภทของรายการ",
    "รายละเอียด",
    "จำนวนเงิน",
    "วันและเวลาการทำรายการ",
}

# Inside the "รายละเอียด" section a line looks like:
#   "จาก ธนาคารไทยพาณิชย์ เบอร์บัญชี"  followed by the account number on the next line
#   "ไปยัง ธนาคารKBank เบอร์บัญชี"      followed by the account number on the next line
_FROM_RE = re.compile(r"^จาก\s+(.+?)\s+เบอร์บัญชี\s*$")
_TO_RE = re.compile(r"^ไปยัง\s+(.+?)\s+เบอร์บัญชี\s*$")
_INLINE_FROM_RE = re.compile(r"จาก\s+(.+?)\s+เบอร์บัญชี\s*(\S+)")
_INLINE_TO_RE = re.compile(r"ไปยัง\s+(.+?)\s+เบอร์บัญชี\s*(\S+)")
_INLINE_AMOUNT_RE = re.compile(r"^จำนวนเงิน\s+(.+)$")


def _header_key(line: str) -> str | None:
    """Return the section header key if `line` is a header, otherwise None."""
    key = line.strip().rstrip(":：").strip()
    return key if key in SECTION_HEADERS else None


def _split_inline_section(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if ":" in stripped or "：" in stripped:
        label, value = re.split(r"[:：]", stripped, maxsplit=1)
        label = label.strip()
        value = value.strip()
        if label in SECTION_HEADERS and value:
            return label, value.strip()
    match = _INLINE_AMOUNT_RE.match(stripped)
    if match:
        return "จำนวนเงิน", match.group(1).strip()
    return None


def _parse_details(value_lines: list[str], raw: dict) -> None:
    """Parse the multi-line "รายละเอียด" section into from/to bank and account."""
    i = 0
    n = len(value_lines)
    while i < n:
        line = value_lines[i]
        match_from = _FROM_RE.match(line)
        match_to = _TO_RE.match(line)
        if match_from:
            raw["from_bank"] = match_from.group(1).strip()
            if i + 1 < n:
                raw["from_account"] = value_lines[i + 1].strip()
                i += 2
                continue
        elif match_to:
            raw["to_bank"] = match_to.group(1).strip()
            if i + 1 < n:
                raw["to_account"] = value_lines[i + 1].strip()
                i += 2
                continue
        else:
            inline_from = _INLINE_FROM_RE.search(line)
            inline_to = _INLINE_TO_RE.search(line)
            if inline_from:
                raw["from_bank"] = inline_from.group(1).strip()
                raw["from_account"] = inline_from.group(2).strip()
            elif inline_to:
                raw["to_bank"] = inline_to.group(1).strip()
                raw["to_account"] = inline_to.group(2).strip()
        i += 1


def extract_fields(text: str) -> dict[str, str]:
    """Extract raw fields from an SCB email body into a dict keyed by canonical name.

    Each section header collects the non-empty lines that follow it (up to the next
    header) as its value. The "รายละเอียด" section is parsed into discrete
    from_bank/from_account/to_bank/to_account fields. Nothing is discarded: the full
    details section is also preserved under "details" for use as a description.
    """
    lines = text.split("\n")
    n = len(lines)
    sections: list[tuple[str, list[str]]] = []

    i = 0
    while i < n:
        inline = _split_inline_section(lines[i])
        if inline is not None:
            key, value = inline
            i += 1
            value_lines = [value] if value else []
            if key == "รายละเอียด":
                while i < n and _header_key(lines[i]) is None and _split_inline_section(lines[i]) is None:
                    stripped = lines[i].strip()
                    if stripped:
                        value_lines.append(stripped)
                    i += 1
            sections.append((key, value_lines))
            continue
        key = _header_key(lines[i])
        if key is None:
            i += 1
            continue
        i += 1
        value_lines: list[str] = []
        while i < n and _header_key(lines[i]) is None and _split_inline_section(lines[i]) is None:
            stripped = lines[i].strip()
            if stripped:
                value_lines.append(stripped)
            i += 1
        sections.append((key, value_lines))

    raw: dict[str, str] = {}
    for key, value_lines in sections:
        if key == "รายละเอียด":
            _parse_details(value_lines, raw)
            raw["details"] = re.sub(r"\s+", " ", " ".join(value_lines)).strip()
            continue
        canonical = to_canonical(key)
        if canonical:
            raw[canonical] = re.sub(r"\s+", " ", " ".join(value_lines)).strip()

    return raw
