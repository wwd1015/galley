"""The deterministic checks: original document against the rendered PDF."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from galley.verify.model import DocModel, Section
from galley.verify.normalize import context, heading_key, normalize, number_counts, numbers, tokens
from galley.verify.settings import VerifySettings


@dataclass
class Finding:
    """One flagged difference. ``fails`` findings make their check fail unless accepted."""

    id: str
    kind: str
    message: str
    fails: bool = True
    numeric: bool = False
    location: str = ""
    source: str = ""
    candidate: str = ""
    accepted: bool = False
    accepted_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class CheckResult:
    id: str
    title: str
    hard: bool
    summary: str = ""
    skipped: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        if any(f.fails and not f.accepted for f in self.findings):
            return "fail" if self.hard else "advisory"
        return "warn" if any(not f.accepted for f in self.findings) else "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "hard": self.hard,
            "status": self.status,
            "summary": self.summary,
            "findings": [f.as_dict() for f in self.findings],
        }


def _digest(*parts: str) -> str:
    return hashlib.sha1(" ".join(parts).encode()).hexdigest()[:8]


def _snippet(words: list[str], start: int, end: int, pad: int = 6) -> str:
    before = " ".join(words[max(0, start - pad) : start])
    middle = " ".join(words[start:end])
    after = " ".join(words[end : end + pad])
    return f"{before} [{middle}] {after}".strip()


def _aligned(section: Section, candidate: DocModel, used: set[int]) -> list[str] | None:
    """Tokens of the candidate section with the same heading, if there is one."""
    if section.heading is None:
        return None
    key = heading_key(section.heading.text)
    for index, other in enumerate(candidate.sections):
        if index in used or other.heading is None:
            continue
        if heading_key(other.heading.text) == key:
            used.add(index)
            return tokens(other.text)
    return None


def check_text(source: DocModel, candidate: DocModel, settings: VerifySettings) -> CheckResult:
    """Every word of the source appears in the PDF; missing passages are located."""
    result = CheckResult("text", "Text coverage", hard=True)
    source_tokens = tokens(source.text)
    candidate_tokens = tokens(candidate.text)
    if not source_tokens:
        result.skipped, result.summary = True, "the source has no text"
        return result
    missing = Counter(source_tokens) - Counter(candidate_tokens)
    coverage = 1 - sum(missing.values()) / len(source_tokens)
    haystack = " " + " ".join(candidate_tokens) + " "

    used: set[int] = set()
    longest = 0
    for section in source.sections:
        words = tokens(section.text)
        if not words:
            continue
        target = _aligned(section, candidate, used)
        other = target if target is not None else candidate_tokens
        matcher = SequenceMatcher(None, words, other, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag not in ("delete", "replace"):
                continue
            run = words[i1:i2]
            # Text that merely moved (a footnote, a caption) is present elsewhere.
            if len(run) > 1 and f" {' '.join(run)} " in haystack:
                continue
            if len(run) == 1 and missing[run[0]] <= 0:
                continue
            longest = max(longest, len(run))
            result.findings.append(
                Finding(
                    id=f"text-{_digest(*run)}",
                    kind="missing-text",
                    message=f"{len(run)} consecutive source word(s) not found in the PDF",
                    fails=len(run) >= settings.max_missing_run,
                    location=section.location,
                    source=_snippet(words, i1, i2),
                    candidate=_snippet(other, j1, j2) if other else "",
                )
            )
    if coverage < settings.min_coverage:
        result.findings.insert(
            0,
            Finding(
                id="text-coverage",
                kind="coverage",
                message=(
                    f"{coverage:.2%} of source words are present; "
                    f"at least {settings.min_coverage:.2%} required"
                ),
            ),
        )
    result.summary = (
        f"{coverage:.2%} of {len(source_tokens)} source words present; "
        f"longest missing run {longest} word(s)"
    )
    return result


def check_numbers(source: DocModel, candidate: DocModel, settings: VerifySettings) -> CheckResult:
    """The numbers in the source and in the PDF are the same multiset."""
    result = CheckResult("numbers", "Numbers", hard=True)
    labels = settings.labels
    source_text = normalize(source.text)
    candidate_text = normalize(candidate.text)
    source_counts = number_counts(source_text, labels)
    candidate_counts = number_counts(candidate_text, labels)
    front_matter: set[str] = set()
    for value in candidate.metadata:
        front_matter.update(number_counts(value))

    def first_context(text: str, value: str) -> str:
        from galley.verify.normalize import strip_labels

        stripped = strip_labels(text, labels)
        for found, offset in numbers(stripped):
            if found == value:
                return context(stripped, offset)
        return ""

    def location(value: str) -> str:
        for section in source.sections:
            body = (section.heading.text + " " if section.heading else "") + section.text
            if value in number_counts(body, labels):
                return section.location
        return ""

    for value, count in sorted((source_counts - candidate_counts).items()):
        result.findings.append(
            Finding(
                id=f"numbers-missing-{value}",
                kind="missing-number",
                message=(
                    f"{value} appears {source_counts[value]}x in the source "
                    f"but {candidate_counts[value]}x in the PDF"
                ),
                numeric=True,
                location=location(value),
                source=first_context(source_text, value),
                candidate=first_context(candidate_text, value),
            )
        )
        del count
    for value in sorted(candidate_counts - source_counts):
        if value in front_matter:
            continue
        result.findings.append(
            Finding(
                id=f"numbers-extra-{value}",
                kind="extra-number",
                message=(
                    f"{value} appears {candidate_counts[value]}x in the PDF "
                    f"but {source_counts[value]}x in the source"
                ),
                fails=settings.extra_numbers_fail,
                numeric=True,
                source=first_context(source_text, value),
                candidate=first_context(candidate_text, value),
            )
        )
    result.summary = (
        f"{sum(source_counts.values())} numbers in the source, "
        f"{len([f for f in result.findings if f.kind == 'missing-number'])} missing or altered, "
        f"{len([f for f in result.findings if f.kind == 'extra-number'])} unexpected in the PDF"
    )
    return result


def check_structure(source: DocModel, candidate: DocModel) -> CheckResult:
    """The heading trees are identical in order, level and text."""
    result = CheckResult("structure", "Structure", hard=True)

    def tree(model: DocModel) -> list[tuple[int, str]]:
        headings = model.headings
        top = min((h.level for h in headings), default=1)
        return [(h.level - top + 1, heading_key(h.text)) for h in headings]

    def label(model: DocModel, index: int) -> str:
        heading = model.headings[index]
        return f"{'#' * heading.level} {heading.text}"

    a, b = tree(source), tree(candidate)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        before = "; ".join(label(source, i) for i in range(i1, i2))
        after = "; ".join(label(candidate, j) for j in range(j1, j2))
        message = {
            "delete": "heading missing from the PDF",
            "insert": "heading in the PDF that the source lacks",
            "replace": "heading changed (text or level)",
        }[tag]
        result.findings.append(
            Finding(
                id=f"structure-{_digest(before, after)}",
                kind=f"heading-{tag}",
                message=message,
                source=before,
                candidate=after,
            )
        )
    result.summary = f"{len(a)} heading(s) in the source, {len(b)} in the PDF"
    return result


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(tokens(a)), " ".join(tokens(b)), autojunk=False).ratio()


def check_exhibits(source: DocModel, candidate: DocModel, settings: VerifySettings) -> CheckResult:
    """Figures and tables are all there and their captions match."""
    result = CheckResult("exhibits", "Exhibits", hard=True)
    for kind, source_count, candidate_count in (
        ("figure", max(source.figures, len(source.figure_captions)), candidate.figures),
        ("table", max(source.tables, len(source.table_captions)), candidate.tables),
    ):
        if source_count != candidate_count:
            result.findings.append(
                Finding(
                    id=f"exhibits-{kind}-count",
                    kind=f"{kind}-count",
                    message=f"{source_count} {kind}(s) in the source, {candidate_count} in the PDF",
                    source=str(source_count),
                    candidate=str(candidate_count),
                )
            )
    for kind, source_captions, candidate_captions in (
        ("figure", source.figure_captions, candidate.figure_captions),
        ("table", source.table_captions, candidate.table_captions),
    ):
        for caption in source_captions:
            best = max(candidate_captions, key=lambda c: _similarity(caption, c), default="")
            score = _similarity(caption, best) if best else 0.0
            if score < settings.min_caption_similarity:
                result.findings.append(
                    Finding(
                        id=f"exhibits-caption-{_digest(caption)}",
                        kind=f"{kind}-caption",
                        message=f"{kind} caption is {score:.0%} similar to its closest match",
                        source=caption,
                        candidate=best,
                    )
                )
    result.summary = (
        f"figures {max(source.figures, len(source.figure_captions))}/{candidate.figures}, "
        f"tables {max(source.tables, len(source.table_captions))}/{candidate.tables} "
        "(source/PDF)"
    )
    return result


def check_notes(source: DocModel, candidate: DocModel) -> CheckResult:
    """Footnote counts agree; citations still unresolved are listed."""
    result = CheckResult("notes", "Footnotes and citations", hard=True)
    if source.footnotes is None or candidate.footnotes is None:
        result.summary = "footnotes could not be identified; count not compared"
    else:
        if len(source.footnotes) != len(candidate.footnotes):
            result.findings.append(
                Finding(
                    id="notes-footnote-count",
                    kind="footnote-count",
                    message=(
                        f"{len(source.footnotes)} footnote(s) in the source, "
                        f"{len(candidate.footnotes)} in the PDF"
                    ),
                    source=str(len(source.footnotes)),
                    candidate=str(len(candidate.footnotes)),
                )
            )
        result.summary = (
            f"footnotes {len(source.footnotes)}/{len(candidate.footnotes)} (source/PDF)"
        )
    if source.bibliography.strip():
        listed = Counter(tokens(source.bibliography))
        missing = listed - Counter(tokens(candidate.bibliography))
        share = 1 - sum(missing.values()) / max(sum(listed.values()), 1)
        if share < 0.9:
            result.findings.append(
                Finding(
                    id="notes-reference-list",
                    kind="reference-list",
                    message=(
                        f"only {share:.0%} of the words in the source's reference list appear in "
                        "the PDF's bibliography; check that every entry is cited"
                    ),
                    fails=False,
                    source=source.bibliography[:160],
                    candidate=candidate.bibliography[:160],
                )
            )
    for key in candidate.unresolved_citations:
        result.findings.append(
            Finding(
                id=f"notes-citation-{key}",
                kind="unresolved-citation",
                message=f"citation {key} is not matched to a bibliography entry",
                fails=False,
                candidate=f"[@{key}]",
            )
        )
    if candidate.unresolved_citations:
        result.summary += f"; {len(candidate.unresolved_citations)} unresolved citation(s)"
    return result


def run_checks(
    source: DocModel, candidate: DocModel, settings: VerifySettings
) -> list[CheckResult]:
    return [
        check_text(source, candidate, settings),
        check_numbers(source, candidate, settings),
        check_structure(source, candidate),
        check_exhibits(source, candidate, settings),
        check_notes(source, candidate),
    ]
