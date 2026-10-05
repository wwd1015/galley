"""Phase 3 acceptance: verify catches deliberately injected errors."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from galley import verify
from galley.cli import app

from . import builders

pytestmark = pytest.mark.tex


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("verify")
    source = builders.make_docx(base / "original.docx", builders.make_png(base / "image.png"))
    paper = builders.make_paper(base / "paper")
    return source, paper


def statuses(report: dict[str, object]) -> dict[str, str]:
    checks = report["checks"]
    assert isinstance(checks, list)
    return {c["id"]: c["status"] for c in checks}


def verify_with(
    workspace: tuple[Path, Path], tmp_path: Path, old: str, new: str
) -> dict[str, object]:
    source, paper = workspace
    broken = tmp_path / "paper"
    shutil.copytree(paper, broken, ignore=shutil.ignore_patterns("paper.pdf", "paper.tex"))
    text = (broken / "paper.qmd").read_text(encoding="utf-8")
    assert old in text
    (broken / "paper.qmd").write_text(text.replace(old, new), encoding="utf-8")
    return verify.verify(source, broken)


def test_faithful_paper_passes_and_writes_reports(workspace: tuple[Path, Path]) -> None:
    source, paper = workspace
    report = verify.verify(source, paper)
    assert report["status"] == "pass", (paper / "verify-report.md").read_text(encoding="utf-8")
    assert statuses(report) == {
        "text": "pass", "numbers": "pass", "structure": "pass", "exhibits": "pass",
        "notes": "pass", "visual": "pass",
    }  # fmt: skip
    assert json.loads((paper / "verify-report.json").read_text("utf-8"))["status"] == "pass"
    assert "**PASS**" in (paper / "verify-report.md").read_text(encoding="utf-8")
    assert (paper / "verify-visual" / "source-image1.png").is_file()


def test_catches_dropped_paragraph(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    report = verify_with(workspace, tmp_path, builders.SENSITIVITY, "")
    assert report["status"] == "fail"
    assert statuses(report)["text"] == "fail"
    assert statuses(report)["numbers"] == "pass"


def test_catches_changed_number(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    report = verify_with(workspace, tmp_path, "12.25%", "12.52%")
    assert statuses(report)["numbers"] == "fail"
    checks = report["checks"]
    assert isinstance(checks, list)
    ids = {f["id"] for c in checks for f in c["findings"]}
    assert {"numbers-missing-12.25%", "numbers-extra-12.52%"} <= ids


def test_catches_missing_figure(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    figure = f"![{builders.FIGURE_CAPTION}](figures/source/image1.png){{#fig-buffer width=70%}}"
    report = verify_with(workspace, tmp_path, figure, "")
    assert statuses(report)["exhibits"] == "fail"


def test_catches_renamed_heading(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    report = verify_with(workspace, tmp_path, "## Scope", "## Coverage")
    assert statuses(report)["structure"] == "fail"


def test_cli_exit_codes(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    source, paper = workspace
    runner = CliRunner()
    ok = runner.invoke(app, ["verify", str(source), str(paper), "--no-build"])
    assert ok.exit_code == 0, ok.output
    assert "PASS" in ok.output

    broken = tmp_path / "paper"
    shutil.copytree(paper, broken)
    text = (broken / "paper.qmd").read_text(encoding="utf-8")
    (broken / "paper.qmd").write_text(text.replace("12.25%", "12.52%"), encoding="utf-8")
    failed = runner.invoke(app, ["verify", str(source), str(broken)])
    assert failed.exit_code == 1
    assert "FAIL" in failed.output

    missing = runner.invoke(app, ["verify", str(tmp_path / "nope.docx"), str(paper)])
    assert missing.exit_code == 2
