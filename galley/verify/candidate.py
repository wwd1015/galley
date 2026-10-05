"""Model the rendered Galley PDF, using the intermediate ``.tex`` for structure."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from galley.verify.extract_pdf import extract_pdf
from galley.verify.model import DocModel, Heading
from galley.verify.normalize import normalize
from galley.verify.settings import VerifySettings

LEVELS = {
    "chapter": 0,
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
    "subparagraph": 5,
}
_HEADING = re.compile(
    r"\\(chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\s*(?:\[[^\]]*\])?\{"
)


def _braced(text: str, start: int) -> tuple[str, int]:
    """The contents of the brace group opening at ``start`` and the index after it."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{" and text[index - 1 : index] != "\\":
            depth += 1
        elif text[index] == "}" and text[index - 1 : index] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    return text[start + 1 :], len(text)


def detex(text: str) -> str:
    """Plain text of a short LaTeX fragment such as a heading."""
    text = re.sub(r"\\texorpdfstring\{((?:[^{}]|\{[^{}]*\})*)\}\{[^{}]*\}", r"\1", text)
    text = re.sub(r"\\(?:label|hypertarget)\{[^}]*\}", "", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{", "{", text)
    text = re.sub(r"\\([%&$#_{}])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\s*", "", text)
    text = (
        text.replace("{", "")
        .replace("}", "")
        .replace("~", " ")
        .replace("``", '"')
        .replace("''", '"')
    )
    return normalize(text)


def tex_body(tex: str) -> str:
    start = tex.find("\\begin{document}")
    return tex[start:] if start >= 0 else tex


def tex_headings(tex: str) -> list[Heading]:
    body = tex_body(tex)
    headings: list[Heading] = []
    for match in _HEADING.finditer(body):
        title, _ = _braced(body, match.end() - 1)
        headings.append(Heading(LEVELS[match.group(1)], detex(title)))
    return headings


def tex_footnote_count(tex: str) -> int:
    return len(re.findall(r"\\footnote(?:text)?\s*\{", tex_body(tex)))


def front_matter(document: Path) -> dict[str, Any]:
    match = re.match(r"---\n(.*?)\n---", document.read_text(encoding="utf-8"), re.DOTALL)
    raw = yaml.safe_load(match.group(1)) if match else None
    return raw if isinstance(raw, dict) else {}


def _scalars(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _scalars(v)]
    if isinstance(value, list):
        return [s for v in value for s in _scalars(v)]
    return [] if value is None else [str(value)]


def extract_candidate(
    pdf: Path, tex: Path, document: Path, sources: list[Path], settings: VerifySettings
) -> DocModel:
    """Model what Galley rendered: text from the PDF, heading tree from the ``.tex``."""
    tex_text = tex.read_text(encoding="utf-8") if tex.is_file() else ""
    model = extract_pdf(pdf, settings, headings=tex_headings(tex_text) if tex_text else None)
    if tex_text and model.footnotes is not None:
        # Footnote markers are unreliable in PDF text; the count comes from the .tex.
        count = tex_footnote_count(tex_text)
        notes = model.footnotes[:count]
        model.footnotes = notes + [""] * (count - len(notes))
    matter = front_matter(document)
    model.metadata = [v for k, val in matter.items() if k != "abstract" for v in _scalars(val)]
    todo: set[str] = set()
    for source in sources:
        todo.update(re.findall(r"@(TODO-\d+)", source.read_text(encoding="utf-8")))
    model.unresolved_citations = sorted(todo, key=lambda key: int(key.split("-")[1]))
    return model
