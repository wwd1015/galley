"""Reduce a PDF to a :class:`DocModel`: what a reader actually sees on the page."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from galley.verify.model import DocModel, Heading, Section
from galley.verify.normalize import (
    heading_key,
    join_lines,
    normalize,
    strip_heading_number,
    tokens,
)
from galley.verify.settings import VerifySettings

SUPERSCRIPT = 1
BOLD = 16


@dataclass
class Block:
    """One text block (roughly a paragraph) with the facts the heuristics need."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    lines: list[str]
    size: float
    bold: bool
    starts_with_marker: bool
    page_width: float
    page_height: float

    @property
    def text(self) -> str:
        return join_lines(self.lines)

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def caption_pattern(labels: list[str]) -> re.Pattern[str]:
    """``Figure 3. Caption`` / ``Table 2: Caption``. The punctuation tells a caption
    from a sentence that merely starts with ``Figure 3 shows``."""
    words = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.compile(
        rf"^(?:{words})\s*\d+(?:\.\d+)*\s*(?:[.:]|\s-)\s*(?P<text>\S.*)$",
        re.IGNORECASE | re.DOTALL,
    )


Rect = tuple[float, float, float, float]


def read_graphics(pdf: Path) -> dict[int, list[Rect]]:
    """Bounding boxes of images and vector drawings on each page (rules excluded)."""
    graphics: dict[int, list[Rect]] = {}
    with pymupdf.open(pdf) as document:
        for page_number, page in enumerate(document, start=1):
            rects = [tuple(info["bbox"]) for info in page.get_image_info()]
            rects += [tuple(rect) for rect in page.cluster_drawings()]
            graphics[page_number] = [r for r in rects if r[3] - r[1] > 20 and r[2] - r[0] > 20]
    return graphics


def read_blocks(pdf: Path, marker: str | None = None) -> list[Block]:
    """Text blocks in reading order, with footnote markers removed from the text.

    With ``marker`` (a format string taking the number) markers are kept in that form.
    """
    blocks: list[Block] = []
    with pymupdf.open(pdf) as document:
        for page_number, page in enumerate(document, start=1):
            rect = page.rect
            for raw in page.get_text("dict", sort=True)["blocks"]:
                if raw.get("type") != 0:
                    continue
                lines: list[str] = []
                sizes: Counter[float] = Counter()
                bold_chars = total_chars = 0
                starts_with_marker = False
                for line_index, line in enumerate(raw["lines"]):
                    parts: list[str] = []
                    line_size = max((span["size"] for span in line["spans"]), default=0.0)
                    for span_index, span in enumerate(line["spans"]):
                        text = span["text"]
                        # A footnote marker: digits set superscript or clearly smaller.
                        is_marker = text.strip().isdigit() and (
                            bool(span["flags"] & SUPERSCRIPT) or span["size"] < 0.8 * line_size
                        )
                        if is_marker:
                            if line_index == 0 and span_index == 0:
                                starts_with_marker = True
                            if marker is not None:
                                parts.append(marker.format(text.strip()))
                            continue
                        parts.append(text)
                        count = len(text.strip())
                        sizes[round(span["size"], 1)] += count
                        total_chars += count
                        if span["flags"] & BOLD or "bold" in span["font"].lower():
                            bold_chars += count
                    if "".join(parts).strip():
                        lines.append("".join(parts))
                if not lines or not sizes:
                    continue
                x0, y0, x1, y1 = raw["bbox"]
                blocks.append(
                    Block(
                        page=page_number,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        lines=lines,
                        size=sizes.most_common(1)[0][0],
                        bold=total_chars > 0 and bold_chars / total_chars > 0.6,
                        starts_with_marker=starts_with_marker,
                        page_width=rect.width,
                        page_height=rect.height,
                    )
                )
    return blocks


def drop_furniture(blocks: list[Block], zone: float) -> list[Block]:
    """Remove running headers, footers and page numbers."""
    pages = {b.page for b in blocks}

    def in_zone(block: Block) -> bool:
        return block.y1 < block.page_height * zone or block.y0 > block.page_height * (1 - zone)

    def key(block: Block) -> str:
        return re.sub(r"\d+", "#", normalize(block.text))

    repeated = Counter(key(b) for b in blocks if in_zone(b))
    threshold = max(2, len(pages) // 2)
    kept: list[Block] = []
    for block in blocks:
        if in_zone(block):
            text = normalize(block.text)
            if repeated[key(block)] >= threshold:
                continue
            if re.fullmatch(r"(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?", text, re.IGNORECASE):
                continue
        kept.append(block)
    return kept


def body_size(blocks: list[Block]) -> float:
    sizes: Counter[float] = Counter()
    for block in blocks:
        sizes[block.size] += len(block.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def figure_band(
    caption: Block, graphics: dict[int, list[Rect]], settings: VerifySettings
) -> tuple[float, float] | None:
    """Vertical extent of the figure a caption belongs to, or None if no graphic is near.

    The figure is the graphic nearest its caption, on the side the template puts figures.
    """
    rects = graphics.get(caption.page, [])
    if settings.caption_below:
        near = [r for r in rects if r[3] <= caption.y0 + 6 and caption.y0 - r[3] < 60]
    else:
        near = [r for r in rects if r[1] >= caption.y1 - 6 and r[1] - caption.y1 < 60]
    if not near:
        return None
    top, bottom = min(r[1] for r in near), max(r[3] for r in near)
    # Graphics that overlap the band (a legend box, a second panel) extend it.
    for rect in rects:
        if rect[1] < bottom and rect[3] > top and rect not in near:
            top, bottom = min(top, rect[1]), max(bottom, rect[3])
    top, bottom = top - 12, bottom + 12
    if settings.caption_below:
        bottom = min(bottom, caption.y0)
    else:
        top = max(top, caption.y1)
    return top, bottom


def drop_figure_text(
    blocks: list[Block], graphics: dict[int, list[Rect]], settings: VerifySettings
) -> tuple[list[Block], int]:
    """Remove text that is part of a figure (axis labels, tick values, legends):
    any text in the figure's vertical band is not the document's prose."""
    pattern = caption_pattern(settings.figure_labels)
    removed: set[int] = set()
    for caption in blocks:
        if not pattern.match(normalize(caption.text)):
            continue
        band = figure_band(caption, graphics, settings)
        if band is None:
            continue
        for index, block in enumerate(blocks):
            if block.page == caption.page and block.y0 >= band[0] and block.y1 <= band[1]:
                removed.add(index)
    return [b for i, b in enumerate(blocks) if i not in removed], len(removed)


def detect_headings(pdf: Path, blocks: list[Block]) -> list[Heading]:
    """Headings of a source PDF: its outline if it has one, else larger or bold short lines."""
    with pymupdf.open(pdf) as document:
        outline = document.get_toc()
    if outline:
        return [Heading(level, normalize(title)) for level, title, *_ in outline]
    base = body_size(blocks)
    larger = sorted({b.size for b in blocks if b.size > base * 1.1}, reverse=True)
    levels = {size: rank + 1 for rank, size in enumerate(larger)}
    # The largest text on the first page is the document title, not a heading.
    largest = max((b.size for b in blocks), default=0.0)
    title = next((b for b in blocks if b.page == 1 and b.size == largest), None)
    headings: list[Heading] = []
    for block in blocks:
        text = normalize(block.text)
        if block is title or len(text) > 120 or len(block.lines) > 2:
            continue
        if block.size in levels:
            headings.append(Heading(levels[block.size], text))
        elif block.bold and block.size >= base and not text.endswith((".", ":", ";", ",")):
            headings.append(Heading(len(levels) + 1, text))
    return headings


def split_sections(blocks: list[Block], headings: list[Heading]) -> list[Section]:
    """Assign block text to headings by finding each heading, in order, in the text."""
    sections: list[Section] = [Section(heading=None, location="p. 1")]
    pending = list(headings)
    for block in blocks:
        text = block.text
        key = heading_key(pending[0].text) if pending else ""
        block_key = heading_key(text)
        if key and (block_key == key or block_key.startswith(key + " ")):
            sections.append(Section(heading=pending.pop(0), location=f"p. {block.page}"))
            # A run-in heading shares its block with the sentence that follows it.
            words = strip_heading_number(normalize(text)).split()
            needed = len(key.split())
            consumed = index = 0
            while index < len(words) and consumed < needed:
                consumed += len(tokens(words[index]))
                index += 1
            sections[-1].text = " ".join(words[index:]).lstrip(".: ")
            continue
        current = sections[-1]
        current.text = f"{current.text}\n{text}" if current.text else text
    return sections


def captions(blocks: list[Block], labels: list[str]) -> list[str]:
    pattern = caption_pattern(labels)
    found: list[str] = []
    for block in blocks:
        match = pattern.match(normalize(block.text))
        if match:
            found.append(match.group("text").strip())
    return found


def footnotes(blocks: list[Block]) -> list[str]:
    """Blocks that open with a superscript number and are set smaller than the body."""
    base = body_size(blocks)
    return [normalize(b.text) for b in blocks if b.starts_with_marker and b.size < base]


def extract_pdf(
    pdf: Path, settings: VerifySettings, headings: list[Heading] | None = None
) -> DocModel:
    """Model a PDF. ``headings`` overrides detection when the structure is known."""
    blocks = drop_furniture(read_blocks(pdf), settings.furniture_zone)
    notes = footnotes(blocks)
    figure_captions = captions(blocks, settings.figure_labels)
    table_captions = captions(blocks, settings.table_labels)
    blocks, _ = drop_figure_text(blocks, read_graphics(pdf), settings)
    found = headings if headings is not None else detect_headings(pdf, blocks)
    return DocModel(
        sections=split_sections(blocks, found),
        figure_captions=figure_captions,
        table_captions=table_captions,
        figures=len(figure_captions),
        tables=len(table_captions),
        footnotes=notes,
    )
