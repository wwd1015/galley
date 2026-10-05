"""Text normalisation shared by both sides of a verification."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Code points, so that no look-alike character hides in this file.
_QUOTES: dict[int, str] = {
    0x2018: "'",  # left single quote
    0x2019: "'",  # right single quote
    0x201A: "'",
    0x201C: '"',  # left double quote
    0x201D: '"',  # right double quote
    0x201E: '"',
    0x2010: "-",  # hyphen
    0x2011: "-",  # non-breaking hyphen
    0x2012: "-",  # figure dash
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
    0x2212: "-",  # minus sign
    0x00A0: " ",  # no-break space
    0x00AD: "",  # soft hyphen
}
_WORD_HYPHEN = re.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])")
_TOKEN = re.compile(r"[^\W_]+(?:[.,'][^\W_]+)*")
_NUMBER = re.compile(
    r"(?<![\w.,])(?P<sign>-)?(?P<currency>[$€£])?"
    r"(?P<digits>\d[\d,]*(?:\.\d+)?)"
    r"\s?(?P<unit>%|bps?\b)?"
)
_NUMBER_AFTER_WORD = re.compile(
    r"(?P<currency>[$€£])?(?P<digits>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>%|bps?\b)?"
)
_HEADING_NUMBER = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2})*\.?|[A-Z]\.\d{1,2}(?:\.\d{1,2})*)\s+(?=\S)"
)


def normalize(text: str) -> str:
    """Unify ligatures, quotes, dashes, soft hyphens and whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip()


def join_lines(lines: list[str]) -> str:
    """Join typeset lines, undoing end-of-line hyphenation."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and line[:1].islower() and out[-2:-1].isalpha():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return out


def strip_labels(text: str, labels: list[str]) -> str:
    """Drop the number after label words: ``Figure 3`` -> ``Figure``."""
    if not labels:
        return text
    words = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.sub(rf"\b({words})\s*\d+(?:\.\d+)*", r"\1", text, flags=re.IGNORECASE)


def strip_heading_number(text: str) -> str:
    """``1.2 Scope`` -> ``Scope``."""
    return _HEADING_NUMBER.sub("", text, count=1).strip()


def tokens(text: str) -> list[str]:
    """Lower-case word tokens; hyphenated compounds are joined (``run-off`` = ``runoff``)."""
    text = _WORD_HYPHEN.sub("", normalize(text)).lower()
    return _TOKEN.findall(text)


def heading_key(text: str) -> str:
    return " ".join(tokens(strip_heading_number(normalize(text))))


def _canonical(match: re.Match[str], signed: bool) -> str:
    # Digits are kept exactly as written: 3.50 and 3.5 are different on the page.
    digits = match.group("digits").replace(",", "")
    unit = match.group("unit") or ""
    if unit.startswith("bp"):
        unit = "bp"
    sign = "-" if signed and match.groupdict().get("sign") else ""
    return f"{sign}{match.group('currency') or ''}{digits}{unit}"


def numbers(text: str, labels: list[str] | None = None) -> list[tuple[str, int]]:
    """Every numeric token as ``(canonical form, offset in the normalised text)``.

    ``12,400`` -> ``12400``; ``5 %`` -> ``5%``; ``25 bps`` -> ``25bp``;
    ``$1.2m`` -> ``$1.2``. A leading minus counts only where it cannot be a range dash.
    """
    text = strip_labels(normalize(text), labels or [])
    found: list[tuple[str, int]] = []
    position = 0
    while position < len(text):
        match = _NUMBER.search(text, position)
        glued = _NUMBER_AFTER_WORD.search(text, position)
        if match is None and glued is None:
            break
        # Numbers glued to letters or dashes (WP-014, Q3) are still numbers.
        if glued is not None and (match is None or glued.start() < match.start()):
            found.append((_canonical(glued, signed=False), glued.start()))
            position = glued.end()
        elif match is not None:
            found.append((_canonical(match, signed=True), match.start()))
            position = match.end()
    return found


def number_counts(text: str, labels: list[str] | None = None) -> Counter[str]:
    return Counter(value for value, _ in numbers(text, labels))


def context(text: str, offset: int, width: int = 60) -> str:
    """The text around ``offset``, for showing a mismatch in a report."""
    start = max(0, offset - width)
    end = min(len(text), offset + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")
