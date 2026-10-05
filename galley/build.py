"""``galley build``: verify data, render, run checks, write ``build-report.json``."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf
import yaml

from galley import manifest, tools
from galley.config import PaperConfig, load_paper_config

REPORT = "build-report.json"
DEFAULT_DOCUMENT = "paper.qmd"
NEEDS_DATA = re.compile(r"galley-status\s*[:=]\s*\"?needs-data")
_CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
_BIB_KEY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")


class BuildError(Exception):
    """The build could not run at all (as opposed to running and failing checks)."""


@dataclass
class BuildResult:
    report: dict[str, Any]
    report_path: Path
    pdf: Path | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def main_document(paper_dir: Path) -> str:
    """The document `galley build` renders: the first entry of project.render."""
    project = paper_dir / "_quarto.yml"
    if project.is_file():
        raw = yaml.safe_load(project.read_text(encoding="utf-8")) or {}
        render = (raw.get("project") or {}).get("render") if isinstance(raw, dict) else None
        if isinstance(render, list) and render:
            return str(render[0])
    return DEFAULT_DOCUMENT


def source_files(paper_dir: Path, document: str) -> list[Path]:
    """The main document and the section files it may include."""
    files = [paper_dir / document]
    files.extend(sorted((paper_dir / "sections").glob("*.qmd")))
    return [f for f in files if f.is_file()]


def unresolved_references(tex: str) -> list[str]:
    """Cross-references Quarto or LaTeX could not resolve."""
    missing = {f"@{ref}" for ref in re.findall(r"\?@([\w:.-]+)", tex)}
    labels = set(re.findall(r"\\label\{([^}]+)\}", tex))
    missing.update(
        ref for ref in re.findall(r"\\(?:eq|page|auto)?ref\{([^}]+)\}", tex) if ref not in labels
    )
    return sorted(missing)


def undefined_citations(tex: str, bibliographies: list[Path]) -> list[str]:
    """Citation keys used in the LaTeX but absent from every bibliography file."""
    cited: set[str] = set()
    for group in _CITE.findall(tex):
        cited.update(key.strip() for key in group.split(",") if key.strip())
    known: set[str] = set()
    for bib in bibliographies:
        if bib.is_file():
            known.update(_BIB_KEY.findall(bib.read_text(encoding="utf-8", errors="replace")))
    return sorted(cited - known)


def figures_needing_data(sources: list[Path]) -> list[str]:
    found: list[str] = []
    for source in sources:
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if NEEDS_DATA.search(line):
                found.append(f"{source.name}:{number}")
    return found


def chart_fonts(paper_dir: Path, document: str, required: str) -> list[str]:
    """Chart PDFs that do not embed the template font ``required``."""
    stem = Path(document).stem
    charts = sorted(paper_dir.glob(f"{stem}_files/figure-pdf/*.pdf"))
    charts += sorted(paper_dir.glob(f"_freeze/{stem}/figure-pdf/*.pdf"))
    wrong: list[str] = []
    for chart in charts:
        with pymupdf.open(chart) as pdf:
            fonts = {font[3] for page in pdf for font in page.get_fonts()}
        if fonts and not any(required.lower() in name.lower() for name in fonts):
            wrong.append(chart.relative_to(paper_dir).as_posix())
    return wrong


def bibliography_files(paper_dir: Path, document: str) -> list[Path]:
    names: list[str] = []
    for candidate in (paper_dir / document, paper_dir / "_quarto.yml"):
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        if candidate.suffix == ".qmd":
            match = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
            text = match.group(1) if match else ""
        raw = yaml.safe_load(text) or {}
        value = raw.get("bibliography") if isinstance(raw, dict) else None
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, list):
            names.extend(str(v) for v in value)
    return [paper_dir / name for name in names]


def render(
    paper_dir: Path, config: PaperConfig, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    quarto = shutil.which("quarto")
    if quarto is None:
        raise BuildError("quarto not found on PATH; run `galley doctor`")
    return subprocess.run(
        [quarto, "render", *(extra_args or [])],
        cwd=paper_dir,
        env=tools.render_env(config.engine),
        capture_output=True,
        text=True,
        check=False,
    )


Progress = Callable[[str], None]


def _quiet(_step: str) -> None:
    """Default progress hook: say nothing."""


def build(
    paper_dir: Path, *, now: datetime | None = None, progress: Progress = _quiet
) -> BuildResult:
    """Run the full build for the paper in ``paper_dir``.

    ``progress`` is called with the name of each step as it starts.
    """
    paper_dir = paper_dir.resolve()
    config = load_paper_config(paper_dir)
    document = main_document(paper_dir)
    if not (paper_dir / document).is_file():
        raise BuildError(f"{document} not found in {paper_dir}")
    stem = Path(document).stem
    problems: list[str] = []
    checks: dict[str, Any] = {}

    # 1. Data must match the manifest before any code runs on it.
    progress("Checking data against the manifest")
    try:
        data_problems = manifest.verify(paper_dir)
        data_hashes = manifest.hashes(paper_dir)
    except manifest.ManifestError as exc:
        data_problems, data_hashes = [str(exc)], {}
    checks["manifest"] = data_problems
    problems.extend(f"data: {p}" for p in data_problems)

    pdf: Path | None = None
    pages = 0
    if not data_problems:
        # 2. Render.
        progress("Rendering the PDF")
        result = render(paper_dir, config)
        candidate = paper_dir / f"{stem}.pdf"
        if result.returncode != 0 or not candidate.is_file():
            tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-30:])
            problems.append(f"render failed:\n{tail}")
        else:
            pdf = candidate
            with pymupdf.open(pdf) as rendered:
                pages = rendered.page_count
            # 3. Checks on what was rendered.
            progress("Checking references, citations and chart fonts")
            tex_path = paper_dir / f"{stem}.tex"
            tex = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
            checks["unresolved-references"] = unresolved_references(tex)
            checks["undefined-citations"] = (
                undefined_citations(tex, bibliography_files(paper_dir, document))
                if config.cite_method != "citeproc"
                else sorted(set(re.findall(r"citation (\S+) not found", result.stderr)))
            )
            required_font = str(config.style.get("font-check", ""))
            checks["chart-fonts"] = (
                chart_fonts(paper_dir, document, required_font)
                if required_font and config.style.get("pgf")
                else []
            )
            problems.extend(f"unresolved reference: {r}" for r in checks["unresolved-references"])
            problems.extend(f"undefined citation: {c}" for c in checks["undefined-citations"])
            problems.extend(
                f"chart does not use the template font {required_font}: {c}"
                for c in checks["chart-fonts"]
            )
    # Reported, not failed: converted figures still waiting for their data.
    checks["needs-data-figures"] = figures_needing_data(source_files(paper_dir, document))

    report: dict[str, Any] = {
        "template": config.template,
        "document": document,
        "git": {"sha": tools.git_sha(paper_dir)},
        "data": data_hashes,
        "tools": tools.tool_versions(config.engine),
        "checks": checks,
        "output": {"pdf": pdf.name if pdf else None, "pages": pages},
        "ok": not problems,
        # The only field that differs between two builds of the same checkout.
        "generated-at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    report_path = paper_dir / REPORT
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BuildResult(report=report, report_path=report_path, pdf=pdf, problems=problems)
