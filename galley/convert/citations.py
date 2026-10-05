"""Turn in-text citations into Quarto citation syntax, matched against a ``.bib`` file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", re.DOTALL)
_FIELD = re.compile(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)", re.DOTALL)
_PARENTHETICAL = re.compile(r"\(([^()]*?\b(?:19|20)\d{2}[a-z]?[^()]*?)\)")
_ONE_CITATION = re.compile(r"^(?P<author>[^\d()]{2,90}?),?\s+(?P<year>(?:19|20)\d{2})[a-z]?$")
_NAME = r"[A-Z][\w'.-]*"
_NARRATIVE = re.compile(
    rf"((?:{_NAME})(?:(?:,? and | & |, | )(?:{_NAME}|et al\.?))*) \(((?:19|20)\d{{2}})[a-z]?\)"
)
_STOPWORDS = {"and", "et", "al", "the", "of", "on", "for", "see", "also", "e.g", "cf", "in"}


@dataclass(frozen=True)
class BibEntry:
    key: str
    year: str
    names: frozenset[str]


@dataclass
class CitationResult:
    text: str
    matched: dict[str, str] = field(default_factory=dict)  # original text -> key
    unresolved: dict[str, str] = field(default_factory=dict)  # TODO-n -> original text


def _words(text: str) -> set[str]:
    cleaned = re.sub(r"[{}\\\"']", "", text)
    return {w.lower() for w in re.findall(r"[^\W\d_]{2,}", cleaned)} - _STOPWORDS


def parse_bib(path: Path) -> list[BibEntry]:
    """Entries with the year and every word of the author (or editor/institution) names."""
    if not path.is_file():
        return []
    entries: list[BibEntry] = []
    for _, key, body in _ENTRY.findall(path.read_text(encoding="utf-8", errors="replace")):
        fields = {name.lower(): value.strip('{}" \n') for name, value in _FIELD.findall(body)}
        year = re.search(r"(?:19|20)\d{2}", fields.get("year", "") or fields.get("date", ""))
        names = _words(fields.get("author", "") or fields.get("editor", ""))
        names |= _words(fields.get("institution", "")) if not names else set()
        if year and names:
            entries.append(BibEntry(key=key, year=year.group(0), names=frozenset(names)))
    return entries


def match(author: str, year: str, entries: list[BibEntry]) -> str | None:
    """The single bibliography entry this citation can mean, if exactly one fits."""
    cited = _words(author)
    if not cited:
        return None
    fits = [e.key for e in entries if e.year == year and cited <= e.names]
    return fits[0] if len(fits) == 1 else None


def convert_citations(markdown: str, entries: list[BibEntry]) -> CitationResult:
    """Rewrite ``(Author 2013)`` as ``[@key]``; unmatched ones become ``[@TODO-n]``.

    Code blocks, chunks and front matter are left alone. A parenthesis is only
    treated as a citation when every ``;``-separated part reads ``Author Year``.
    """
    result = CitationResult(text="")
    counter = 0

    def replace(found: re.Match[str]) -> str:
        nonlocal counter
        parts = [part.strip() for part in found.group(1).split(";")]
        parsed = [
            _ONE_CITATION.match(re.sub(r"^(?:see(?: also)?|cf\.?|e\.g\.,?)\s+", "", p))
            for p in parts
        ]
        if not all(parsed):
            return found.group(0)
        keys: list[str] = []
        unmatched = False
        for part, item in zip(parts, parsed, strict=True):
            assert item is not None
            key = match(item.group("author"), item.group("year"), entries)
            if key is None:
                counter += 1
                key = f"TODO-{counter}"
                result.unresolved[key] = part
                unmatched = True
            else:
                result.matched[part] = key
            keys.append(f"@{key}")
        comment = f"<!-- original: {found.group(0)} -->" if unmatched else ""
        return f"[{'; '.join(keys)}]{comment}"

    def narrative(found: re.Match[str]) -> str:
        """``Diamond and Dybvig (1983)`` -> ``@diamond1983``, only when it matches an entry."""
        words = found.group(1).split(" ")
        # The run of capitalised words may start before the names ("See Diamond ...").
        for start in range(len(words)):
            if words[start].lower().strip(".,") in _STOPWORDS:
                continue
            author = " ".join(words[start:])
            key = match(author, found.group(2), entries)
            if key is not None:
                result.matched[f"{author} ({found.group(2)})"] = key
                lead = " ".join(words[:start])
                return f"{lead} @{key}".strip()
        return found.group(0)

    out: list[str] = []
    in_code = in_front_matter = False
    for number, line in enumerate(markdown.splitlines()):
        if number == 0 and line.strip() == "---":
            in_front_matter = True
        elif in_front_matter and line.strip() == "---":
            in_front_matter = False
        elif line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and not in_front_matter:
            line = _NARRATIVE.sub(narrative, _PARENTHETICAL.sub(replace, line))
        out.append(line)
    result.text = "\n".join(out) + ("\n" if markdown.endswith("\n") else "")
    return result
