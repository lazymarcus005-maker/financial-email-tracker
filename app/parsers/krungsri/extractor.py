"""Krungsri Field Extractor - pull `Label:` / value pairs out of a text block.

Krungsri notifications differ from KBank: the field label sits on its own line
ending with a colon, and the value follows on the next line. A value may span
multiple lines (e.g. a counterparty name with a parenthetical on the following
line) or be entirely empty (e.g. a blank memo).
"""

import re


def is_label_line(line: str) -> bool:
    """Return True if `line` is a standalone field label ending with a colon.

    Value lines such as "03/07/2569 19:21:13" contain colons but do not END
    with one, so they are not mistaken for labels.
    """
    stripped = line.strip()
    if not (stripped.endswith(":") or stripped.endswith("：")):
        return False
    label = stripped.rstrip(":：").strip()
    return bool(label) and "\n" not in label


def extract_fields(text: str) -> list[tuple[str, str]]:
    """Extract (label, value) pairs from the email body.

    A label line is followed by zero or more value lines; value collection stops
    at the next label line. Continuation lines are joined with a single space.
    Empty values (a label immediately followed by another label or EOF) yield "".
    """
    fields: list[tuple[str, str]] = []
    lines = text.split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        if not is_label_line(line):
            i += 1
            continue

        label = line.rstrip(":：").strip()
        value_parts: list[str] = []
        i += 1
        while i < n:
            next_line = lines[i].strip()
            if is_label_line(next_line):
                break
            if next_line:
                value_parts.append(next_line)
            i += 1

        fields.append((label, re.sub(r"\s+", " ", " ".join(value_parts)).strip()))

    return fields
