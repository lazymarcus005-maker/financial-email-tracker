"""KBank Section Detector - locate the primary Thai or English transaction-detail block."""

import re
from dataclasses import dataclass

from app.parsers.kbank.extractor import is_label_line

THAI_CHAR_RE = re.compile(r"[฀-๿]")


@dataclass
class DetectionResult:
    language: str  # "th" or "en"
    section_text: str


def _line_language(line: str) -> str:
    return "th" if THAI_CHAR_RE.search(line) else "en"


def detect_section(text: str) -> DetectionResult:
    """Split the email into contiguous same-language runs of label:value lines
    and return the longest run.

    KBank notifications are often bilingual (a Thai block followed by an
    English block, or vice versa). The field extractor and alias mapper only
    need one consistent-language block to work on.
    """
    segments: list[tuple[str, list[str]]] = []
    current_lang = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not is_label_line(stripped):
            continue

        lang = _line_language(stripped)
        if lang == current_lang:
            current_lines.append(stripped)
        else:
            if current_lines:
                segments.append((current_lang, current_lines))
            current_lang = lang
            current_lines = [stripped]

    if current_lines:
        segments.append((current_lang, current_lines))

    if not segments:
        language = "th" if THAI_CHAR_RE.search(text) else "en"
        return DetectionResult(language=language, section_text=text)

    segments.sort(key=lambda seg: (len(seg[1]), seg[0] == "en"), reverse=True)
    best_language, best_lines = segments[0]
    return DetectionResult(language=best_language, section_text="\n".join(best_lines))
