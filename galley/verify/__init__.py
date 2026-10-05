"""``galley verify``: compare the original document with the rendered Galley PDF."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from galley import build
from galley.verify import visual
from galley.verify.candidate import extract_candidate
from galley.verify.checks import CheckResult, run_checks
from galley.verify.extract_docx import extract_docx
from galley.verify.extract_pdf import extract_pdf
from galley.verify.model import DocModel, split_bibliography
from galley.verify.report import apply_acceptances, build_report, write_reports
from galley.verify.settings import VerifySettings, load_settings

__all__ = ["VerifyError", "VerifySettings", "extract_source", "load_settings", "verify"]


class VerifyError(Exception):
    """Verification could not run (as opposed to running and finding differences)."""


def extract_source(source: Path, settings: VerifySettings) -> DocModel:
    suffix = source.suffix.lower()
    if suffix == ".docx":
        return extract_docx(source, settings)
    if suffix == ".pdf":
        return extract_pdf(source, settings)
    raise VerifyError(f"unsupported source type '{suffix}': expected .docx or .pdf")


def verify(
    source: Path,
    paper_dir: Path,
    *,
    rebuild: bool = True,
    settings: VerifySettings | None = None,
    now: datetime | None = None,
    progress: build.Progress = build._quiet,
) -> dict[str, Any]:
    """Run every check and write the reports into ``paper_dir``; return the report."""
    paper_dir = paper_dir.resolve()
    if not source.is_file():
        raise VerifyError(f"source document not found: {source}")
    settings = settings or load_settings(
        paper_dir / "verify.yaml" if (paper_dir / "verify.yaml").is_file() else None
    )
    document = build.main_document(paper_dir)
    stem = Path(document).stem
    pdf = paper_dir / f"{stem}.pdf"
    if rebuild:
        try:
            result = build.build(paper_dir, now=now, progress=progress)
        except build.BuildError as exc:
            raise VerifyError(str(exc)) from exc
        if result.pdf is None:
            raise VerifyError("the paper did not render:\n" + "\n".join(result.problems))
    if not pdf.is_file():
        raise VerifyError(f"{pdf.name} not found; run `galley build` first")

    progress("Reading the original document")
    source_model = extract_source(source, settings)
    progress("Reading the rendered PDF")
    candidate_model = extract_candidate(
        pdf,
        paper_dir / f"{stem}.tex",
        paper_dir / document,
        build.source_files(paper_dir, document),
        settings,
    )
    progress("Comparing text, numbers, structure and exhibits")
    split_bibliography(source_model)
    split_bibliography(candidate_model)
    checks: list[CheckResult] = run_checks(source_model, candidate_model, settings)
    rejected = apply_acceptances(checks, paper_dir)
    images = visual.export(source, pdf, paper_dir, settings)
    checks.append(
        CheckResult(
            "visual",
            "Visual",
            hard=False,
            summary=f"{len(images)} image(s) written for side-by-side review",
        )
    )
    report = build_report(
        checks,
        source=source,
        paper_dir=paper_dir,
        pdf=pdf,
        thresholds=settings.as_dict(),
        rejected=rejected,
        visual=images,
        now=now,
    )
    write_reports(report, paper_dir)
    return report
