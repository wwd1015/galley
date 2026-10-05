"""Reduce a Word document to a :class:`DocModel`."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from galley.verify.model import DocModel, Heading, Section
from galley.verify.normalize import normalize
from galley.verify.settings import VerifySettings

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
FRONT_MATTER_STYLES = {"title", "subtitle", "author", "date"}
CAPTION_STYLES = {"caption", "tablecaption", "imagecaption", "figurecaption"}


def _q(tag: str) -> str:
    return f"{{{W}}}{tag}"


def style_names(archive: zipfile.ZipFile) -> dict[str, str]:
    """Style id -> display name (``Heading1`` -> ``heading 1``)."""
    if "word/styles.xml" not in archive.namelist():
        return {}
    root = ElementTree.fromstring(archive.read("word/styles.xml"))
    names: dict[str, str] = {}
    for style in root.iter(_q("style")):
        name = style.find("w:name", NS)
        style_id = style.get(_q("styleId"))
        if style_id and name is not None:
            names[style_id] = name.get(_q("val"), style_id)
    return names


def paragraph_text(paragraph: ElementTree.Element) -> str:
    """Visible text of a paragraph, including field results and hyperlinks."""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == _q("t"):
            parts.append(node.text or "")
        elif node.tag in (_q("tab"), _q("br")):
            parts.append(" ")
    return normalize("".join(parts))


def paragraph_style(paragraph: ElementTree.Element, names: dict[str, str]) -> str:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    if style is None:
        return ""
    style_id = style.get(_q("val"), "")
    return names.get(style_id, style_id)


def count_drawings(element: ElementTree.Element) -> int:
    return sum(1 for node in element.iter() if node.tag in (_q("drawing"), _q("pict")))


def _caption_kind(text: str, styled: bool, settings: VerifySettings) -> tuple[str, str] | None:
    """``("figure", caption text)`` if the paragraph is an exhibit caption.

    Without a caption style, punctuation after the number is what tells a
    caption from a sentence that starts with ``Figure 3 shows``.
    """
    separator = r"\s*[.:\-]?\s+" if styled else r"\s*(?:[.:]|\s-)\s*"
    for kind, labels in (("figure", settings.figure_labels), ("table", settings.table_labels)):
        words = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
        match = re.match(rf"^(?:{words})\s*\d+(?:\.\d+)*{separator}(\S.*)$", text, re.IGNORECASE)
        if match:
            return kind, match.group(1).strip()
    return None


def read_footnotes(archive: zipfile.ZipFile) -> list[str]:
    if "word/footnotes.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("word/footnotes.xml"))
    notes: list[str] = []
    for note in root.iter(_q("footnote")):
        if note.get(_q("type")) in ("separator", "continuationSeparator", "continuationNotice"):
            continue
        text = normalize(" ".join(paragraph_text(p) for p in note.iter(_q("p"))))
        if text:
            notes.append(text)
    return notes


def extract_docx(path: Path, settings: VerifySettings) -> DocModel:
    with zipfile.ZipFile(path) as archive:
        names = style_names(archive)
        body = ElementTree.fromstring(archive.read("word/document.xml")).find("w:body", NS)
        notes = read_footnotes(archive)
    model = DocModel(sections=[Section(heading=None, location="¶ 1")], footnotes=notes)
    if body is None:
        return model

    def append(text: str) -> None:
        current = model.sections[-1]
        current.text = f"{current.text}\n{text}" if current.text else text

    index = 0
    for element in body:
        if element.tag == _q("tbl"):
            model.tables += 1
            for row in element.iter(_q("tr")):
                cells = [
                    normalize(" ".join(paragraph_text(p) for p in cell.iter(_q("p"))))
                    for cell in row.iter(_q("tc"))
                ]
                append(" ".join(cell for cell in cells if cell))
            continue
        if element.tag != _q("p"):
            continue
        index += 1
        model.figures += count_drawings(element)
        text = paragraph_text(element)
        if not text:
            continue
        style = paragraph_style(element, names).lower()
        compact = style.replace(" ", "")
        heading = re.fullmatch(r"heading\s*(\d)", style)
        if heading:
            model.sections.append(
                Section(Heading(int(heading.group(1)), text), location=f"¶ {index}")
            )
            continue
        caption = _caption_kind(text, compact in CAPTION_STYLES, settings)
        if caption and (compact in CAPTION_STYLES or len(text) < 300):
            kind, caption_text = caption
            (model.figure_captions if kind == "figure" else model.table_captions).append(
                caption_text
            )
        append(text)
    if notes:
        # Footnote text is content too; it has no place in the body's flow in a .docx.
        model.sections.append(Section(heading=None, text="\n".join(notes), location="footnotes"))
    return model
