"""KBank Field Extractor - pull `Label : Value` lines out of a text block."""

import re

LABEL_LINE_RE = re.compile(r"^(?P<label>[^\n:：]{1,60}?)\s*[:：]\s*(?P<value>\S.*?)\s*$")


def is_label_line(line: str) -> bool:
    """Return True if `line` looks like a `Label : Value` field line."""
    return bool(LABEL_LINE_RE.match(line.strip()))


def extract_fields(text: str) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from `Label : Value` lines in text.

    Order is preserved and duplicate labels are NOT merged here - callers
    (e.g. the canonical mapper) decide how to resolve duplicates.
    """
    fields = []
    for line in text.split("\n"):
        match = LABEL_LINE_RE.match(line.strip())
        if match:
            fields.append((match.group("label").strip(), match.group("value").strip()))
    return fields
