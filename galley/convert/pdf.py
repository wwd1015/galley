"""PDF path: layout-aware extraction with PyMuPDF.

Charts in a PDF are images or vector drawings with no data behind them, so
every figure becomes a static image marked ``needs-data``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from galley.convert.docx import write_tables, yaml_string
from galley.convert.model import Conversion
from galley.verify.extract_pdf import (
    Block,
    Rect,
    body_size,
    caption_pattern,
    detect_headings,
    drop_furniture,
    figure_band,
    read_blocks,
    read_graphics,
)
from galley.verify.normalize import heading_key, join_lines, normalize, strip_heading_number
from galley.verify.settings import VerifySettings

FOOTNOTE = "\x02{}\x03"
_FOOTNOTE = re.compile("\x02(\\d+)\x03")
# Bullet, triangular bullet, white bullet, hyphen bullet, bullet operator, en dash.
_BULLET_CHARS = "".join(chr(c) for c in (0x2022, 0x2023, 0x25E6, 0x2043, 0x2219, 0x2013))
_BULLET = re.compile(rf"^\s*(?:[{_BULLET_CHARS}]|[-*])\s+")
_ORDERED = re.compile(r"^\s*(\d{1,2})[.)]\s+")
_ESCAPE = re.compile(r"([\\`*_$<>\[\]|~^])")


def escape(text: str) -> str:
    """Escape Markdown syntax, then turn footnote placeholders into references."""
    text = _ESCAPE.sub(r"\\\1", text)
    text = re.sub(r"(?<![\w.])@", r"\\@", text)
    return _FOOTNOTE.sub(r"[^\1]", text)


def list_items(lines: list[str], pattern: re.Pattern[str]) -> list[str] | None:
    """Items of a block whose lines form a list, or None if it is not one."""
    if not lines or not pattern.match(lines[0]):
        return None
    items: list[list[str]] = []
    for line in lines:
        if pattern.match(line):
            items.append([pattern.sub("", line, count=1)])
        else:
            items[-1].append(line)
    return [join_lines(item) for item in items]


def horizontal_rules(pdf: Path) -> dict[int, list[Rect]]:
    """Thin horizontal lines on each page: the rules of booktabs-style tables."""
    rules: dict[int, list[Rect]] = {}
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, start=1):
            found: list[Rect] = []
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                if rect.height < 3 and rect.width > 50:
                    found.append((rect.x0, rect.y0, rect.x1, rect.y1))
            rules[number] = sorted(found, key=lambda r: r[1])
    return rules


def is_body(block: Block, text_width: float) -> bool:
    return block.width >= 0.6 * text_width and len(block.text) > 60


def table_region(
    caption: Block, later: list[Block], rules: list[Rect], text_width: float
) -> tuple[float, float]:
    """Vertical extent of the table under ``caption``."""
    limit = caption.page_height
    for block in later:
        if block.page == caption.page and block.y0 > caption.y1 and is_body(block, text_width):
            limit = block.y0
            break
    below = [r for r in rules if caption.y1 - 2 <= r[1] < limit]
    if len(below) >= 2:
        return caption.y1, below[-1][3] + 2
    return caption.y1, limit


CELL_GAP = 7.0  # points: a wider gap between words starts a new cell


def rows_from_words(words: list[tuple[float, float, float, float, str]]) -> list[list[str]]:
    """Rebuild a table from positioned words: rows by baseline, columns by overlap."""
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        middle = (word[1] + word[3]) / 2
        if lines and abs((lines[-1][0][1] + lines[-1][0][3]) / 2 - middle) < 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    # Cells: runs of words on a line separated by less than CELL_GAP.
    table: list[list[tuple[float, float, str]]] = []
    for line in lines:
        cells: list[tuple[float, float, str]] = []
        for x0, _y0, x1, _y1, text in sorted(line):
            if cells and x0 - cells[-1][1] < CELL_GAP:
                cells[-1] = (cells[-1][0], x1, f"{cells[-1][2]} {text}")
            else:
                cells.append((x0, x1, text))
        table.append(cells)
    # Columns: cells whose horizontal extents overlap share a column.
    columns: list[tuple[float, float]] = []
    for x0, x1, _ in sorted(cell for row in table for cell in row):
        if columns and x0 < columns[-1][1]:
            columns[-1] = (columns[-1][0], max(columns[-1][1], x1))
        else:
            columns.append((x0, x1))
    rows: list[list[str]] = []
    for row_cells in table:
        row = [""] * len(columns)
        for x0, x1, text in row_cells:
            column = max(
                range(len(columns)),
                key=lambda i: min(x1, columns[i][1]) - max(x0, columns[i][0]),
            )
            row[column] = f"{row[column]} {text}".strip()
        rows.append(row)
    return rows


def extract_table(pdf: Path, page_number: int, region: tuple[float, float]) -> list[list[str]]:
    with pymupdf.open(pdf) as document:
        page = document[page_number - 1]
        clip = pymupdf.Rect(0, region[0], page.rect.width, region[1])
        words = [(w[0], w[1], w[2], w[3], str(w[4])) for w in page.get_text("words", clip=clip)]
    rows = rows_from_words(words)
    # One column is a list of lines, not a table.
    return rows if rows and len(rows[0]) > 1 else []


def crop_figure(pdf: Path, page_number: int, band: tuple[float, float], target: Path) -> None:
    with pymupdf.open(pdf) as document:
        page = document[page_number - 1]
        clip = pymupdf.Rect(0, max(0, band[0]), page.rect.width, band[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(dpi=200, clip=clip).save(target)


def convert_pdf(source: Path, out: Path, settings: VerifySettings) -> Conversion:
    """Extract ``source`` into ``out``; return the conversion with its Markdown body."""
    conversion = Conversion()
    blocks = drop_furniture(read_blocks(source, marker=FOOTNOTE), settings.furniture_zone)
    graphics = read_graphics(source)
    rules = horizontal_rules(source)
    figure_caption = caption_pattern(settings.figure_labels)
    table_caption = caption_pattern(settings.table_labels)
    base = body_size(blocks)
    text_width = max((b.x1 for b in blocks), default=0) - min((b.x0 for b in blocks), default=0)

    def plain(block: Block) -> str:
        return _FOOTNOTE.sub("", normalize(block.text))

    pending = detect_headings(source, [b for b in blocks if not _FOOTNOTE.match(b.text)])
    pending = [h for h in pending if h.text]
    top = min((h.level for h in pending), default=1)

    # The first page is a title page when it carries no heading of its own.
    first_page = [b for b in blocks if b.page == 1]
    keys = {heading_key(h.text) for h in pending}
    has_heading = any(heading_key(plain(b)) in keys for b in first_page)
    pages = {b.page for b in blocks}
    skip: set[int] = set()
    if first_page:
        title = max(first_page, key=lambda b: b.size)
        conversion.title = plain(title)
        skip.add(id(title))
        if not has_heading and len(pages) > 1:
            leftovers = [plain(b) for b in first_page if b is not title]
            skip.update(id(b) for b in first_page)
            if leftovers:
                conversion.unsure.append(
                    "Title-page text was not carried into the body; map it to front matter "
                    "by hand: " + " / ".join(leftovers)
                )

    # Pass 1: exhibits, so that cross-references and figure text can be resolved.
    figures: dict[str, str] = {}
    tables: dict[str, str] = {}
    exhibit_markdown: dict[int, str] = {}
    for index, block in enumerate(blocks):
        if id(block) in skip:
            continue
        text = plain(block)
        figure = figure_caption.match(text)
        table = table_caption.match(text)
        number = re.search(r"\d+", text)
        if figure and number:
            identifier = f"fig-{number.group(0)}"
            band = figure_band(block, graphics, settings)
            caption = escape(figure.group("text").strip())
            if band is None:
                conversion.unsure.append(
                    f"No graphic found near the caption of {identifier} "
                    f"(p. {block.page}); the caption was kept as text."
                )
                continue
            target = f"figures/source/{identifier}.png"
            crop_figure(source, block.page, band, out / target)
            figures[number.group(0)] = identifier
            conversion.images.append(target)
            conversion.needs_data.append(f"{identifier} ({target}, source p. {block.page})")
            exhibit_markdown[index] = (
                f'![{caption}]({target}){{#{identifier} galley-status="needs-data"}}'
            )
            for other in blocks:
                if other.page == block.page and other.y0 >= band[0] and other.y1 <= band[1]:
                    skip.add(id(other))
        elif table and number:
            identifier = f"tbl-{number.group(0)}"
            region = table_region(block, blocks[index + 1 :], rules.get(block.page, []), text_width)
            rows = extract_table(source, block.page, region)
            if not rows:
                conversion.unsure.append(
                    f"The table under '{text[:60]}' (p. {block.page}) could not be read as "
                    "rows and columns; its text was kept as paragraphs."
                )
                continue
            path = f"data/tables/{identifier}.csv"
            tables[number.group(0)] = identifier
            conversion.tables[path] = rows
            conversion.uses_python = True
            exhibit_markdown[index] = "\n".join(
                [
                    "```{python}",
                    f"#| label: {identifier}",
                    f"#| tbl-cap: {yaml_string(table.group('text'))}",
                    "#| output: asis",
                    f'print(tables.to_latex(tables.read_csv("{path}")))',
                    "```",
                ]
            )
            for other in blocks:
                if other.page == block.page and region[0] <= other.y0 and other.y1 <= region[1]:
                    skip.add(id(other))

    def cross_reference(text: str) -> str:
        def replace(found: re.Match[str]) -> str:
            registry = figures if found.group(1).lower().startswith("fig") else tables
            identifier = registry.get(found.group(2))
            return f"@{identifier}" if identifier else found.group(0)

        return re.sub(r"\b(Figure|Fig\\\.|Table)\s+(\d+)\b", replace, text)

    # Pass 2: the text flow.
    parts: list[str] = []
    notes: dict[str, str] = {}
    for index, block in enumerate(blocks):
        if index in exhibit_markdown:
            parts.append(exhibit_markdown[index])
            continue
        if id(block) in skip:
            continue
        text = plain(block)
        if pending and heading_key(text) == heading_key(pending[0].text):
            heading = pending.pop(0)
            level = heading.level - top + 1
            parts.append("#" * level + " " + escape(strip_heading_number(text)))
            continue
        note = _FOOTNOTE.match(block.text.strip())
        if note and block.size < base:
            notes[note.group(1)] = escape(_FOOTNOTE.sub("", join_lines(block.lines), count=1))
            continue
        bullets = list_items(block.lines, _BULLET)
        ordered = list_items(block.lines, _ORDERED)
        if bullets:
            parts.append("\n".join(f"- {cross_reference(escape(item))}" for item in bullets))
        elif ordered:
            parts.append(
                "\n".join(
                    f"{n}. {cross_reference(escape(item))}" for n, item in enumerate(ordered, 1)
                )
            )
        else:
            parts.append(cross_reference(escape(join_lines(block.lines))))
    for note_number, note_text in notes.items():
        parts.append(f"[^{note_number}]: {note_text.strip()}")
    conversion.body = "\n\n".join(part for part in parts if part.strip()) + "\n"

    write_tables(conversion, out)
    return conversion
