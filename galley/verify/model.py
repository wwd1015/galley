"""The document model both sides of a verification are reduced to."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass
class Section:
    """A heading (None for text before the first heading) and the text under it."""

    heading: Heading | None
    text: str = ""
    location: str = ""


@dataclass
class DocModel:
    """What a reader sees, reduced to the facts the checks compare."""

    sections: list[Section] = field(default_factory=list)
    figure_captions: list[str] = field(default_factory=list)
    table_captions: list[str] = field(default_factory=list)
    figures: int = 0
    tables: int = 0
    # None when the format gives no reliable way to tell footnotes apart.
    footnotes: list[str] | None = None
    unresolved_citations: list[str] = field(default_factory=list)
    # Front-matter values (document id, version, date) that are not body content.
    metadata: list[str] = field(default_factory=list)

    @property
    def headings(self) -> list[Heading]:
        return [s.heading for s in self.sections if s.heading is not None]

    @property
    def text(self) -> str:
        parts: list[str] = []
        for section in self.sections:
            if section.heading is not None:
                parts.append(section.heading.text)
            parts.append(section.text)
        return "\n".join(parts)
