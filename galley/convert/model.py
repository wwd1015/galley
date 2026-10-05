"""What a conversion produces, and the report that explains it."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chart:
    """A chart whose data was recovered from the source document."""

    number: int
    kind: str  # bar | line | area | pie | scatter
    title: str
    header: list[str]
    rows: list[list[str]]
    figure_id: str = ""


@dataclass
class Conversion:
    """Everything extracted from one source document."""

    title: str = ""
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    date: str = ""
    body: str = ""
    tables: dict[str, list[list[str]]] = field(default_factory=dict)  # csv path -> rows
    charts: list[Chart] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    needs_data: list[str] = field(default_factory=list)
    unmapped_styles: Counter[str] = field(default_factory=Counter)
    style_samples: dict[str, str] = field(default_factory=dict)
    unsure: list[str] = field(default_factory=list)
    matched_citations: dict[str, str] = field(default_factory=dict)
    unresolved_citations: dict[str, str] = field(default_factory=dict)
    uses_python: bool = False

    def note_style(self, style: str, sample: str) -> None:
        self.unmapped_styles[style] += 1
        self.style_samples.setdefault(style, sample.strip()[:80])


def render_report(
    conversion: Conversion,
    *,
    source_name: str,
    source_url: str,
    verify_report: dict[str, Any] | None,
    verify_error: str,
) -> str:
    """``conversion-report.md``: everything a person must look at after converting."""
    lines = [
        "# Conversion report",
        "",
        f"Source: `source/{source_name}`"
        + (f" (exported from {source_url})" if source_url else ""),
        "",
        "| Item | Count |",
        "| --- | --- |",
        f"| Tables converted to CSV | {len(conversion.tables)} |",
        f"| Charts rebuilt from embedded data | {len(conversion.charts)} |",
        f"| Figures needing data | {len(conversion.needs_data)} |",
        f"| Unmapped styles | {len(conversion.unmapped_styles)} |",
        f"| Citations matched | {len(conversion.matched_citations)} |",
        f"| Citations unresolved | {len(conversion.unresolved_citations)} |",
        f"| Items the converter was unsure about | {len(conversion.unsure)} |",
        "",
        "## Unmapped styles",
        "",
    ]
    if conversion.unmapped_styles:
        lines += [
            "The text was kept and the style dropped. Add a mapping to "
            "`config/style-map.yaml` (or list the style under `ignore`) and convert again.",
            "",
            "| Style | Uses | First use |",
            "| --- | --- | --- |",
        ]
        for style, count in sorted(conversion.unmapped_styles.items()):
            sample = conversion.style_samples.get(style, "").replace("|", "\\|")
            lines.append(f"| {style} | {count} | {sample} |")
    else:
        lines.append("None.")
    lines += ["", "## Figures needing data", ""]
    if conversion.needs_data:
        lines.append(
            'These are static images marked `galley-status="needs-data"`. Replace each '
            "with a chunk that draws the chart from data."
        )
        lines.append("")
        lines += [f"- `{item}`" for item in conversion.needs_data]
    else:
        lines.append("None.")
    lines += ["", "## Unresolved citations", ""]
    if conversion.unresolved_citations:
        lines.append("Add the entry to `references.bib` and replace the `TODO` key.")
        lines.append("")
        lines += [f"- `[@{key}]`: {text}" for key, text in conversion.unresolved_citations.items()]
    else:
        lines.append("None.")
    lines += ["", "## Unsure", ""]
    lines += [f"- {item}" for item in conversion.unsure] or ["Nothing."]
    lines += ["", "## Verify", ""]
    if verify_error:
        lines.append(f"`galley verify` could not run: {verify_error}")
    elif verify_report is None:
        lines.append("Not run (`--no-verify`). Run `galley verify source/" + source_name + " .`.")
    else:
        lines.append(
            f"**{str(verify_report['status']).upper()}**. Details are in `verify-report.md`."
        )
        lines += ["", "| Check | Result | Summary |", "| --- | --- | --- |"]
        for check in verify_report["checks"]:
            lines.append(f"| {check['title']} | {check['status']} | {check['summary']} |")
        failing = [
            f
            for c in verify_report["checks"]
            if c["status"] == "fail"
            for f in c["findings"]
            if f["fails"] and not f["accepted"]
        ]
        if failing:
            lines += ["", "Failures to resolve or explain:", ""]
            lines += [f"- `{f['id']}`: {f['message']}" for f in failing]
    return "\n".join(lines) + "\n"
