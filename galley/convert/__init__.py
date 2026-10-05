"""``galley convert``: bring a Word, Google Docs or PDF whitepaper into Galley."""

from __future__ import annotations

import re
import shutil
import stat
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from galley import build, manifest, scaffold
from galley import verify as verifier
from galley.config import ConfigError, read_paper_config, tool_config_dir
from galley.convert.ast import PandocError
from galley.convert.citations import convert_citations, parse_bib
from galley.convert.docx import convert_docx
from galley.convert.model import Chart, Conversion, render_report
from galley.convert.pdf import convert_pdf
from galley.template import EXTENSION_DIR, PAPER_CONFIG
from galley.verify.settings import load_settings

__all__ = ["ConvertError", "convert", "export_google_doc"]

REPORT = "conversion-report.md"
GOOGLE_DOC = re.compile(r"https://docs\.google\.com/document/d/([\w-]+)")
SETUP = """\
```{{python}}
import pandas as pd
from galley import paper, tables

paper.setup()
{imports}```
"""
EXHIBITS_HEADER = '''\
"""Charts rebuilt by `galley convert` from data embedded in the source document.

Each function is a pure ``(df) -> Figure``. The first column holds the
categories and every other column is one series. Restyle them freely.
"""

from __future__ import annotations

import pandas as pd
from matplotlib.figure import Figure


def _chart(df: pd.DataFrame, kind: str) -> Figure:
    # Build the Figure directly (not through pyplot) so a chunk shows it once.
    figure = Figure()
    axes = figure.subplots()
    categories = df.iloc[:, 0].astype(str)
    series = [column for column in df.columns[1:]]
    if kind == "pie":
        axes.pie(df[series[0]], labels=categories, autopct="%1.0f%%")
        return figure
    width = 0.8 / max(len(series), 1)
    for position, column in enumerate(series):
        if kind == "bar":
            offsets = [i + position * width for i in range(len(df))]
            axes.bar(offsets, df[column], width=width, label=str(column))
        elif kind == "area":
            axes.fill_between(categories, df[column], alpha=0.5, label=str(column))
        elif kind == "scatter":
            axes.scatter(df.iloc[:, 0], df[column], label=str(column))
        else:
            axes.plot(categories, df[column], label=str(column))
    if kind == "bar":
        centre = width * (len(series) - 1) / 2
        axes.set_xticks([i + centre for i in range(len(df))], categories)
    if len(series) > 1:
        axes.legend()
    return figure
'''


class ConvertError(Exception):
    """The conversion could not run."""


def export_google_doc(url: str, target: Path) -> Path:
    """Download a link-shared Google Doc as ``.docx``."""
    found = GOOGLE_DOC.match(url)
    if not found:
        raise ConvertError(f"not a Google Docs URL: {url}")
    export = f"https://docs.google.com/document/d/{found.group(1)}/export?format=docx"
    try:
        with urllib.request.urlopen(export, timeout=60) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConvertError(
            f"could not export the Google Doc ({exc}). Download it as .docx "
            "(File > Download > Microsoft Word) and run "
            "`galley convert <file>.docx --source-url <url>`."
        ) from exc
    if not data.startswith(b"PK"):
        raise ConvertError(
            "the Google Doc is not shared by link, so it cannot be exported without signing in. "
            "Download it as .docx and run `galley convert <file>.docx --source-url <url>`."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def load_style_map(path: Path | None = None) -> dict[str, Any]:
    path = path or tool_config_dir() / "style-map.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConvertError(f"{path} is not a YAML mapping")
    return raw


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def drop_reference_list(body: str, headings: list[str]) -> tuple[str, bool]:
    """Remove the source's own reference list: its title line and everything under it.

    The title may be a heading or, from a PDF, a line on its own. Footnote
    definitions that follow the list are kept.
    """
    names = {h.lower() for h in headings}
    lines = body.splitlines()
    for index, line in enumerate(lines):
        found = re.match(r"^(#*)\s*(.*?)\s*(?:\{.*\})?$", line)
        if not found or found.group(2).strip().lower() not in names:
            continue
        level = len(found.group(1)) or 6
        end = len(lines)
        for later in range(index + 1, len(lines)):
            other = re.match(r"^(#+)\s", lines[later])
            if other and len(other.group(1)) <= level:
                end = later
                break
        notes = [text for text in lines[index:end] if text.startswith("[^")]
        kept = lines[:index] + [n for note in notes for n in (note, "")] + lines[end:]
        return "\n".join(kept).rstrip() + "\n", True
    return body, False


def split_sections(body: str) -> list[tuple[str, str]]:
    """Split a body at top-level headings into ``(file name, text)`` pairs."""
    chunks: list[list[str]] = [[]]
    in_code = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("# ") and any(c.strip() for c in chunks[-1]):
            chunks.append([])
        chunks[-1].append(line)
    sections: list[tuple[str, str]] = []
    for number, chunk in enumerate(chunks, start=1):
        heading = next((line[2:] for line in chunk if line.startswith("# ")), "front")
        title = re.sub(r"\s*\{.*\}\s*$", "", heading)
        sections.append(
            (f"{number:02d}-{slugify(title)[:40]}.qmd", "\n".join(chunk).strip() + "\n")
        )
    return sections


def write_exhibits(charts: list[Chart], out: Path) -> None:
    parts = [EXHIBITS_HEADER]
    for chart in charts:
        doc = (chart.title or f"Chart {chart.number} from the source document").replace('"', "'")
        parts.append(
            f"\n\ndef chart_{chart.number}(df: pd.DataFrame) -> Figure:\n"
            f'    """{doc}."""\n'
            f'    return _chart(df, "{chart.kind}")\n'
        )
        target = out / "data" / "charts" / f"chart-{chart.number}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        import csv

        with target.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows([chart.header, *chart.rows])
    (out / "py" / "exhibits" / "converted.py").write_text("".join(parts), encoding="utf-8")


def convert(
    source: Path,
    out: Path,
    *,
    bib: Path | None = None,
    source_url: str = "",
    style_map_path: Path | None = None,
    run_verify: bool = True,
    today: date | None = None,
    progress: build.Progress = build._quiet,
) -> tuple[Conversion, dict[str, Any] | None]:
    """Convert ``source`` into a paper repo at ``out``; return the conversion and verify report."""
    suffix = source.suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise ConvertError(
            f"unsupported input '{suffix}': expected .docx, .pdf or a Google Doc URL"
        )
    if not source.is_file():
        raise ConvertError(f"input not found: {source}")
    if out.exists() and any(out.iterdir()):
        raise ConvertError(f"{out} already exists and is not empty")
    out.mkdir(parents=True, exist_ok=True)
    out = out.resolve()

    style_map = load_style_map(style_map_path)
    settings = load_settings()
    progress("Extracting text, tables, figures and charts")
    try:
        extension = scaffold.vendor_extension(out)
        config = read_paper_config(extension / PAPER_CONFIG)
        scaffold.write_project_files(out, out.name)
        conversion = (
            convert_docx(source, out, style_map, settings)
            if suffix == ".docx"
            else convert_pdf(source, out, settings)
        )
    except (PandocError, ConfigError) as exc:
        raise ConvertError(str(exc)) from exc
    if suffix == ".pdf" and not style_map_path:
        conversion.unsure.append(
            "PDF sources carry no style names, so callouts such as key findings are plain text; "
            "wrap them in the template's divs by hand."
        )

    # Citations, and the source's own reference list.
    progress("Matching citations to the bibliography")
    entries = parse_bib(bib) if bib else []
    cited = convert_citations(conversion.body, entries)
    conversion.body = cited.text
    conversion.matched_citations = cited.matched
    conversion.unresolved_citations = cited.unresolved
    bibliography = bool(bib and cited.matched)
    if bib:
        shutil.copyfile(bib, out / "references.bib")
    else:
        (out / "references.bib").write_text("", encoding="utf-8")
    if bibliography:
        conversion.body, dropped = drop_reference_list(
            conversion.body, [str(h) for h in style_map.get("reference-headings") or []]
        )
        if dropped:
            conversion.unsure.append(
                "The source's reference list was replaced by the bibliography generated from "
                "`references.bib`; check that every entry it listed is cited."
            )

    # The original, kept read-only beside the conversion.
    progress("Writing the paper repo")
    original = out / "source" / f"original{suffix}"
    shutil.copyfile(source, original)
    original.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    if conversion.charts:
        write_exhibits(conversion.charts, out)
    manifest.save(out, [])
    as_of = (today or date.today()).isoformat()
    for path in manifest.data_files(out):
        manifest.add(out, Path(path), source=f"Extracted from source/{original.name}", as_of=as_of)

    # paper.qmd, split into sections/ when long.
    matter_fields: dict[str, Any] = {}
    if conversion.subtitle:
        matter_fields["subtitle"] = conversion.subtitle
    if conversion.authors:
        matter_fields["author"] = conversion.authors
    matter_fields["date"] = conversion.date or as_of
    extra: dict[str, Any] = dict(config.scaffold_metadata)
    if bibliography:
        extra["bibliography"] = "references.bib"
    galley_meta = {"source": f"source/{original.name}"}
    if source_url:
        galley_meta["source-url"] = source_url
    extra["galley-source"] = galley_meta
    title = conversion.title or scaffold.title_from_slug(out.name)
    front = scaffold.front_matter(title, extra, **matter_fields)
    parts = [f"---\n{front}---\n"]
    if conversion.uses_python:
        names = ", ".join(f"chart_{c.number}" for c in conversion.charts)
        imports = f"from exhibits.converted import {names}\n" if names else ""
        parts.append(SETUP.format(imports=imports))
    limit = int(style_map.get("split-sections-over-words", 6000))
    if len(conversion.body.split()) > limit:
        for name, text in split_sections(conversion.body):
            (out / "sections" / name).write_text(text, encoding="utf-8")
            parts.append(f"{{{{< include sections/{name} >}}}}\n")
    else:
        parts.append(conversion.body)
    (out / "paper.qmd").write_text("\n".join(parts), encoding="utf-8")

    # Always finish by verifying against the original.
    report: dict[str, Any] | None = None
    error = ""
    if run_verify:
        try:
            report = verifier.verify(original, out, progress=progress)
        except verifier.VerifyError as exc:
            error = str(exc)
    (out / REPORT).write_text(
        render_report(
            conversion,
            source_name=original.name,
            source_url=source_url,
            verify_report=report,
            verify_error=error,
        ),
        encoding="utf-8",
    )
    assert (out / EXTENSION_DIR).is_dir()
    return conversion, report
