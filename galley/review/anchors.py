"""Where a comment sits in a file: anchors, diff lines and re-anchoring.

GitHub only accepts line comments on lines inside the pull request's diff.
For any other line Galley posts a file-level comment whose body starts with a
hidden anchor naming the line and quoting its first characters; the app reads
the anchor back and shows the comment on that line.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

QUOTE_LENGTH = 60
FUZZY_MATCH = 0.8
_ANCHOR = re.compile(
    r'^\s*<!--\s*galley:anchor\s+line=(\d+)\s+quote="(.*?)"\s*-->\s*\n?', re.DOTALL
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Anchor:
    line: int
    quote: str


def quote_of(text: str) -> str:
    """The part of a line used to find it again: its first characters, trimmed."""
    return text.strip()[:QUOTE_LENGTH]


def make_anchor(line: int, text: str) -> str:
    """The hidden anchor comment for ``text`` on 1-based ``line``."""
    # HTML comments cannot contain "--", and the quote is delimited by double quotes.
    quoted = html.escape(quote_of(text), quote=True).replace("--", "&#45;&#45;")
    return f'<!-- galley:anchor line={line} quote="{quoted}" -->'


def with_anchor(body: str, line: int, text: str) -> str:
    return f"{make_anchor(line, text)}\n{body}"


def parse_anchor(body: str) -> tuple[Anchor | None, str]:
    """Split a comment body into its anchor (if any) and the visible text."""
    found = _ANCHOR.match(body)
    if not found:
        return None, body
    quote = html.unescape(found.group(2).replace("&#45;&#45;", "--"))
    return Anchor(line=int(found.group(1)), quote=quote), body[found.end() :]


def diff_lines(patch: str) -> set[int]:
    """New-side line numbers that appear in a unified diff (added or context lines)."""
    lines: set[int] = set()
    current = 0
    for row in patch.splitlines():
        header = _HUNK.match(row)
        if header:
            current = int(header.group(1))
        elif row.startswith("-") or row.startswith("\\"):
            continue
        elif current and (row.startswith("+") or row.startswith(" ") or row == ""):
            lines.add(current)
            current += 1
    return lines


def hunk_quote(diff_hunk: str) -> str:
    """The text of the line a native review comment was left on: the hunk's last line."""
    rows = [row for row in diff_hunk.splitlines() if not row.startswith("\\")]
    if not rows or _HUNK.match(rows[-1]):
        return ""
    return quote_of(rows[-1][1:])


def locate(lines: list[str], quote: str, line: int | None) -> int | None:
    """Find a comment's 1-based line in the current text, or None if it is outdated.

    The quoted text decides: an exact match nearest the recorded line, else a
    close match. The line number is only trusted when there is no quote.
    """
    if not quote:
        return line if line is not None and 1 <= line <= len(lines) else None
    target = line or 1

    def nearest(candidates: list[int]) -> int:
        return min(candidates, key=lambda number: abs(number - target))

    exact = [n for n, text in enumerate(lines, start=1) if quote_of(text) == quote]
    if exact:
        return nearest(exact)
    prefix = [n for n, text in enumerate(lines, start=1) if text.strip().startswith(quote)]
    if prefix:
        return nearest(prefix)
    close = [
        n
        for n, text in enumerate(lines, start=1)
        if text.strip()
        and SequenceMatcher(None, quote_of(text), quote, autojunk=False).ratio() >= FUZZY_MATCH
    ]
    return nearest(close) if close else None
