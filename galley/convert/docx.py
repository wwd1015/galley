"""Word path: Pandoc for the content, the document's own XML for styles and charts."""

from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from galley.convert import ast
from galley.convert.ast import Node
from galley.convert.model import Chart, Conversion
from galley.verify.settings import VerifySettings

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CHART_MARKER = "GALLEYCHART"
CHART_KINDS = {
    "barChart": "bar", "bar3DChart": "bar", "lineChart": "line", "line3DChart": "line",
    "areaChart": "area", "pieChart": "pie", "doughnutChart": "pie", "scatterChart": "scatter",
}  # fmt: skip
MEDIA_DIR = "figures/source"
UNTYPESETTABLE = {".emf", ".wmf", ".svg", ".gif", ".tif", ".tiff"}


def _points(parent: ElementTree.Element | None) -> list[str]:
    if parent is None:
        return []
    points = sorted(parent.iter(f"{{{C}}}pt"), key=lambda pt: int(pt.get("idx", "0")))
    return [(pt.findtext(f"{{{C}}}v") or "").strip() for pt in points]


def _child(parent: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    for name in names:
        found = parent.find(f"{{{C}}}{name}")
        if found is not None:
            return found
    return None


def parse_chart(xml: bytes, number: int) -> Chart | None:
    """Read a chart's cached data: categories and one column per series."""
    root = ElementTree.fromstring(xml)
    plot = root.find(f".//{{{C}}}plotArea")
    if plot is None:
        return None
    kind = next(
        (
            CHART_KINDS[child.tag.split("}")[1]]
            for child in plot
            if child.tag.split("}")[1] in CHART_KINDS
        ),
        None,
    )
    if kind is None:
        return None
    title = "".join(t.text or "" for t in root.findall(f"./{{{C}}}chart/{{{C}}}title//{{{A}}}t"))
    categories: list[str] = []
    columns: list[tuple[str, list[str]]] = []
    for index, series in enumerate(plot.iter(f"{{{C}}}ser"), start=1):
        name = " ".join(_points(_child(series, "tx"))) or f"Series {index}"
        cats = _points(_child(series, "cat", "xVal"))
        values = _points(_child(series, "val", "yVal"))
        if len(cats) > len(categories):
            categories = cats
        columns.append((name, values))
    if not columns:
        return None
    length = max(len(categories), *(len(values) for _, values in columns))
    categories += [""] * (length - len(categories))
    rows = [
        [categories[i]] + [values[i] if i < len(values) else "" for _, values in columns]
        for i in range(length)
    ]
    return Chart(number, kind, title.strip(), ["category"] + [n for n, _ in columns], rows)


def mark_charts(source: Path, marked: Path) -> list[Chart]:
    """Copy ``source`` with each embedded chart replaced by a text marker Pandoc keeps."""
    charts: list[Chart] = []
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        document = archive.read("word/document.xml").decode("utf-8")
        targets: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in names:
            rels = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
            for rel in rels.iter(f"{{{PKG_REL}}}Relationship"):
                targets[rel.get("Id", "")] = rel.get("Target", "")

        def replace(found: re.Match[str]) -> str:
            ref = re.search(r"<c:chart\b[^>]*\br:id=\"([^\"]+)\"", found.group(0))
            if ref is None:
                return found.group(0)
            target = targets.get(ref.group(1), "")
            member = target.lstrip("/") if target.startswith("/") else f"word/{target}"
            if member not in names:
                return found.group(0)
            chart = parse_chart(archive.read(member), len(charts) + 1)
            if chart is None:
                return found.group(0)
            charts.append(chart)
            return f"<w:t>{CHART_MARKER}{chart.number}</w:t>"

        document = re.sub(r"<w:drawing\b[^>]*>.*?</w:drawing>", replace, document, flags=re.DOTALL)
        with zipfile.ZipFile(marked, "w", zipfile.ZIP_DEFLATED) as out:
            for name in names:
                data = (
                    document.encode("utf-8") if name == "word/document.xml" else archive.read(name)
                )
                out.writestr(name, data)
    return charts


def _label_pattern(labels: list[str]) -> re.Pattern[str]:
    words = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    dashes = "\\-" + chr(0x2013) + chr(0x2014)
    return re.compile(rf"^(?:{words})\s*(\d+)(?:\.\d+)*\s*[.:{dashes}]?\s*", re.IGNORECASE)


def strip_caption_label(inlines: list[Node], pattern: re.Pattern[str]) -> tuple[list[Node], str]:
    """Remove a leading ``Figure 3.`` from caption inlines; return them and the number."""
    text = ast.stringify(inlines)
    found = pattern.match(text)
    if not found:
        return inlines, ""
    remaining = len(found.group(0))
    out = list(inlines)
    while out and remaining > 0:
        piece = ast.stringify(out[0])
        if len(piece) <= remaining:
            remaining -= len(piece)
            out.pop(0)
        else:
            if out[0].get("t") == "Str":
                out[0] = ast.str_(piece[remaining:])
            remaining = 0
    return out, found.group(1)


def caption_inlines(caption: list[Any]) -> list[Node]:
    """Inlines of a Figure or Table caption; Pandoc may wrap them in styled divs."""

    def walk(blocks: list[Node]) -> list[Node]:
        out: list[Node] = []
        for block in blocks:
            if block.get("t") == "Div":
                out.extend(walk(block["c"][1]))
            elif block.get("t") in ("Para", "Plain"):
                if out:
                    out.append(ast.space())
                out.extend(block["c"])
        return out

    return walk(caption[1])


def chunk(options: dict[str, str], code: list[str]) -> Node:
    lines = ["```{python}", *(f"#| {key}: {value}" for key, value in options.items()), *code, "```"]
    return ast.raw_block("\n".join(lines))


def yaml_string(text: str) -> str:
    return json.dumps(text.strip(), ensure_ascii=False)


class DocxTransformer:
    """Rewrites Pandoc's AST of a Word document into Galley constructs."""

    def __init__(
        self,
        conversion: Conversion,
        style_map: dict[str, Any],
        settings: VerifySettings,
        charts: list[Chart],
    ) -> None:
        self.conversion = conversion
        self.styles: dict[str, dict[str, Any]] = style_map.get("styles") or {}
        self.ignore = {str(s).lower() for s in style_map.get("ignore") or []}
        self.charts = {chart.number: chart for chart in charts}
        self.figure_label = _label_pattern(settings.figure_labels)
        self.table_label = _label_pattern(settings.table_labels)
        self.figure_ids: dict[str, str] = {}  # source number -> id
        self.table_ids: dict[str, str] = {}
        self.figure_count = 0
        self.table_count = 0

    # -- ids ---------------------------------------------------------------

    def _new_id(self, prefix: str, number: str, registry: dict[str, str], count: int) -> str:
        candidate = f"{prefix}-{number or count}"
        taken = set(registry.values())
        while candidate in taken:
            count += 1
            candidate = f"{prefix}-{count}"
        if number:
            registry[number] = candidate
        else:
            registry[f"_{count}"] = candidate
        return candidate

    def figure_id(self, number: str) -> str:
        self.figure_count += 1
        return self._new_id("fig", number, self.figure_ids, self.figure_count)

    def table_id(self, number: str) -> str:
        self.table_count += 1
        return self._new_id("tbl", number, self.table_ids, self.table_count)

    # -- blocks ------------------------------------------------------------

    def blocks(self, blocks: list[Node]) -> list[Node]:
        out: list[Node] = []
        index = 0
        while index < len(blocks):
            block = blocks[index]
            following = blocks[index + 1] if index + 1 < len(blocks) else None
            kind = block.get("t")
            marker = re.fullmatch(rf"{CHART_MARKER}(\d+)", ast.stringify(block).strip())
            if kind in ("Para", "Plain") and marker:
                consumed = self.chart(int(marker.group(1)), following, out)
                index += 1 + consumed
                continue
            if kind == "Div":
                out.extend(self.div(block))
            elif kind == "Figure":
                out.append(self.figure(block))
            elif kind == "Table":
                out.append(self.table(block))
            elif kind in ("Para", "Plain") and self._lone_image(block) is not None:
                out.append(self.image_paragraph(block))
            else:
                out.append(block)
            index += 1
        return out

    def div(self, block: Node) -> list[Node]:
        attr, inner = block["c"]
        style = ast.attribute(attr, "custom-style")
        if style is None:
            return [block]
        mapping = self.styles.get(style)
        if mapping and mapping.get("div"):
            return [{"t": "Div", "c": [["", [str(mapping["div"])], []], inner]}]
        if style.lower() not in self.ignore:
            self.conversion.note_style(style, ast.stringify(inner))
        return list(inner)

    @staticmethod
    def _lone_image(block: Node) -> Node | None:
        inlines = [i for i in block["c"] if i.get("t") not in ("Space", "SoftBreak")]
        return inlines[0] if len(inlines) == 1 and inlines[0].get("t") == "Image" else None

    def _image(self, image: Node, caption: list[Node], number: str) -> Node:
        attr, _, (target, _title) = image["c"]
        target = target.replace(f"{MEDIA_DIR}/media/", f"{MEDIA_DIR}/")
        identifier = self.figure_id(number)
        keep = [[k, v] for k, v in attr[2] if k == "width"]
        self.conversion.images.append(target)
        self.conversion.needs_data.append(f"{identifier} ({target})")
        if Path(target).suffix.lower() in UNTYPESETTABLE:
            self.conversion.unsure.append(
                f"`{target}` ({identifier}) is in a format LaTeX cannot include; "
                "export it as PDF or PNG."
            )
        if not caption:
            self.conversion.unsure.append(
                f"`{identifier}` has no caption in the source; write one."
            )
        new_attr = [identifier, [], [*keep, ["galley-status", "needs-data"]]]
        return ast.para([{"t": "Image", "c": [new_attr, caption, [target, ""]]}])

    def figure(self, block: Node) -> Node:
        _attr, caption, content = block["c"]
        images = [
            inline
            for inner in content
            if inner.get("t") in ("Para", "Plain")
            for inline in inner["c"]
            if inline.get("t") == "Image"
        ]
        if not images:
            return block
        inlines = caption_inlines(caption)
        inlines, number = strip_caption_label(inlines, self.figure_label)
        return self._image(images[0], inlines, number)

    def image_paragraph(self, block: Node) -> Node:
        image = self._lone_image(block)
        assert image is not None
        inlines, number = strip_caption_label(list(image["c"][1]), self.figure_label)
        return self._image(image, inlines, number)

    def chart(self, number: int, following: Node | None, out: list[Node]) -> int:
        """Emit a chunk that redraws chart ``number``; return how many extra blocks it used."""
        chart = self.charts[number]
        caption, source_number, consumed = chart.title, "", 0
        if following is not None and following.get("t") in ("Para", "Plain", "Div"):
            text = ast.stringify(following).strip()
            found = self.figure_label.match(text)
            if found:
                caption, source_number, consumed = text[found.end() :].strip(), found.group(1), 1
        chart.figure_id = self.figure_id(source_number)
        options = {"label": chart.figure_id}
        if caption:
            options["fig-cap"] = yaml_string(caption)
        else:
            self.conversion.unsure.append(f"`{chart.figure_id}` has no caption; write one.")
        out.append(
            chunk(
                options,
                [f'chart_{number}(pd.read_csv("data/charts/chart-{number}.csv"))'],
            )
        )
        self.conversion.charts.append(chart)
        self.conversion.uses_python = True
        return consumed

    def table(self, block: Node) -> Node:
        _attr, caption, _specs, head, bodies, _foot = block["c"]
        rows: list[list[str]] = []
        spans = False

        def read(row: list[Any]) -> list[str]:
            nonlocal spans
            cells: list[str] = []
            for _cell_attr, _align, rowspan, colspan, content in row[1]:
                spans = spans or rowspan != 1 or colspan != 1
                cells.append(re.sub(r"\s+", " ", ast.stringify(content)).strip())
            return cells

        rows.extend(read(row) for row in head[1])
        for body in bodies:
            rows.extend(read(row) for row in body[2])
            rows.extend(read(row) for row in body[3])
        inlines = caption_inlines(caption)
        inlines, number = strip_caption_label(inlines, self.table_label)
        caption_text = ast.stringify(inlines).strip()
        identifier = self.table_id(number)
        path = f"data/tables/{identifier}.csv"
        self.conversion.tables[path] = rows
        self.conversion.uses_python = True
        options = {"label": identifier if caption_text else identifier.replace("tbl-", "table-")}
        if caption_text:
            options["tbl-cap"] = yaml_string(caption_text)
        else:
            self.conversion.unsure.append(f"The table in `{path}` has no caption; write one.")
        if spans:
            self.conversion.unsure.append(
                f"The table in `{path}` had merged cells; check its layout against the source."
            )
        options["output"] = "asis"
        return chunk(options, [f'print(tables.to_latex(tables.read_csv("{path}")))'])

    # -- inlines -----------------------------------------------------------

    def inlines(self, inlines: list[Node]) -> list[Node]:
        return self._cross_references(self._unwrap_spans(inlines))

    def _unwrap_spans(self, inlines: list[Node]) -> list[Node]:
        out: list[Node] = []
        for node in inlines:
            if node.get("t") == "Span":
                attr, inner = node["c"]
                style = ast.attribute(attr, "custom-style")
                if style is not None:
                    if style.lower() not in self.ignore:
                        self.conversion.note_style(style, ast.stringify(inner))
                    out.extend(inner)
                    continue
            out.append(node)
        return out

    def _cross_references(self, inlines: list[Node]) -> list[Node]:
        """``Figure 1`` -> ``@fig-1`` wherever exhibit 1 was converted."""
        out: list[Node] = []
        index = 0
        while index < len(inlines):
            node = inlines[index]
            if node.get("t") == "Str" and index + 2 < len(inlines):
                word = str(node["c"])
                registry = None
                if self.figure_label.match(f"{word} 1"):
                    registry = self.figure_ids
                elif self.table_label.match(f"{word} 1"):
                    registry = self.table_ids
                number_node = inlines[index + 2] if index + 2 < len(inlines) else None
                if (
                    registry is not None
                    and inlines[index + 1].get("t") == "Space"
                    and number_node is not None
                    and number_node.get("t") == "Str"
                ):
                    found = re.fullmatch(r"(\d+)([.,;:)\]]*)", str(number_node["c"]))
                    if found and found.group(1) in registry:
                        out.append(ast.raw_inline(f"@{registry[found.group(1)]}"))
                        if found.group(2):
                            out.append(ast.str_(found.group(2)))
                        index += 3
                        continue
            out.append(node)
            index += 1
        return out


def write_tables(conversion: Conversion, out: Path) -> None:
    for relative, rows in conversion.tables.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)


def flatten_media(out: Path) -> None:
    """Pandoc extracts to ``figures/source/media/``; keep the files one level up."""
    media = out / MEDIA_DIR / "media"
    if media.is_dir():
        for item in media.iterdir():
            shutil.move(str(item), str(out / MEDIA_DIR / item.name))
        media.rmdir()


def convert_docx(
    source: Path, out: Path, style_map: dict[str, Any], settings: VerifySettings
) -> Conversion:
    """Extract ``source`` into ``out``; return the conversion with its Markdown body."""
    conversion = Conversion()
    work = out / ".galley-build"
    work.mkdir(parents=True, exist_ok=True)
    marked = work / "marked.docx"
    charts = mark_charts(source, marked)
    document = json.loads(
        ast.pandoc(
            [str(marked), "--from=docx+styles", "--to=json", f"--extract-media={MEDIA_DIR}"], out
        )
    )
    shutil.rmtree(work)
    flatten_media(out)

    meta = document.get("meta", {})
    conversion.title = ast.stringify(meta.get("title")).strip()
    conversion.subtitle = ast.stringify(meta.get("subtitle")).strip()
    conversion.date = ast.stringify(meta.get("date")).strip()
    author = meta.get("author")
    if author is not None:
        items = author["c"] if author.get("t") == "MetaList" else [author]
        conversion.authors = [ast.stringify(item).strip() for item in items]

    transformer = DocxTransformer(conversion, style_map, settings, charts)
    blocks = ast.map_blocks(document["blocks"], block_function=transformer.blocks)
    blocks = ast.map_blocks(blocks, inline_function=transformer.inlines)
    missing = [c for c in charts if c not in conversion.charts]
    for chart in missing:
        conversion.unsure.append(
            f"Embedded chart {chart.number} ({chart.title or 'untitled'}) could not be placed "
            "in the text; its data was not converted."
        )
    conversion.body = ast.to_markdown(blocks, document["pandoc-api-version"], out)
    write_tables(conversion, out)
    return conversion
