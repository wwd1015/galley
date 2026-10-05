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
    # The reference list, kept apart: it is typeset from the .bib in whatever
    # style the template uses, so it is compared loosely, not word for word.
    bibliography: str = ""

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


BIBLIOGRAPHY_TITLES = {"references", "bibliography", "works cited", "literature", "sources"}


def split_bibliography(model: DocModel) -> None:
    """Move the reference list out of the body text into ``model.bibliography``.

    It is either a section whose heading is a bibliography title, or everything
    after the last line that is just such a title.
    """

    def is_title(text: str) -> bool:
        return " ".join(text.lower().split()).strip(" .:") in BIBLIOGRAPHY_TITLES

    for index in range(len(model.sections) - 1, -1, -1):
        section = model.sections[index]
        if section.heading is not None and is_title(section.heading.text):
            model.bibliography = section.text
            del model.sections[index]
            return
    for index in range(len(model.sections) - 1, -1, -1):
        section = model.sections[index]
        lines = section.text.split("\n")
        for line_index in range(len(lines) - 1, -1, -1):
            if is_title(lines[line_index]):
                rest = [s.text for s in model.sections[index + 1 :] if s.heading is None]
                model.bibliography = "\n".join(lines[line_index + 1 :] + rest)
                section.text = "\n".join(lines[:line_index])
                model.sections[index + 1 :] = [
                    s for s in model.sections[index + 1 :] if s.heading is not None
                ]
                return
        if section.heading is not None:
            return
