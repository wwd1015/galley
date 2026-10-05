from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from galley import __version__, template
from galley.cli import app

from .conftest import ROOT

runner = CliRunner()


def copy_repo(tmp_path: Path) -> Path:
    for name in ("template", "config", "_extensions"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"galley {__version__}"


def test_template_check_passes_on_repo() -> None:
    result = runner.invoke(app, ["template", "check", "--root", str(ROOT)])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def test_template_check_fails_then_adopt_repairs(tmp_path: Path) -> None:
    root = copy_repo(tmp_path)
    (root / template.EXTENSION_DIR / "galley.tex").write_text("stale", encoding="utf-8")

    stale = runner.invoke(app, ["template", "check", "--root", str(root)])
    assert stale.exit_code == 1
    assert "out of date" in stale.output

    adopted = runner.invoke(app, ["template", "adopt", "--root", str(root)])
    assert adopted.exit_code == 0, adopted.output
    assert runner.invoke(app, ["template", "check", "--root", str(root)]).exit_code == 0


def test_missing_config_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(app, ["template", "check", "--root", str(tmp_path)])
    assert result.exit_code == 2
    assert "error:" in result.output


def test_template_packages_lists_config_packages() -> None:
    result = runner.invoke(app, ["template", "packages", "--root", str(ROOT)])
    assert result.exit_code == 0
    assert "tcolorbox" in result.output.split()


def test_new_data_verify_and_add(tmp_path: Path) -> None:
    created = runner.invoke(app, ["new", "cli-paper", "--dir", str(tmp_path), "--no-git"])
    assert created.exit_code == 0, created.output
    paper = tmp_path / "cli-paper"
    assert runner.invoke(app, ["data", "verify", str(paper)]).exit_code == 0

    (paper / "data" / "extra.csv").write_text("x\n1\n", encoding="utf-8")
    unlisted = runner.invoke(app, ["data", "verify", str(paper)])
    assert unlisted.exit_code == 1
    assert "not in manifest: data/extra.csv" in unlisted.output

    added = runner.invoke(
        app, ["data", "add", "data/extra.csv", "--paper", str(paper), "--source", "test"]
    )
    assert added.exit_code == 0, added.output
    assert runner.invoke(app, ["data", "verify", str(paper)]).exit_code == 0


def test_packages_from_paper(tmp_path: Path) -> None:
    runner.invoke(app, ["new", "pkg-paper", "--dir", str(tmp_path), "--no-git"])
    result = runner.invoke(app, ["template", "packages", "--paper", str(tmp_path / "pkg-paper")])
    assert result.exit_code == 0
    assert "underscore" in result.output.split()


def test_build_reports_missing_document(tmp_path: Path) -> None:
    runner.invoke(app, ["new", "empty-paper", "--dir", str(tmp_path), "--no-git"])
    (tmp_path / "empty-paper" / "paper.qmd").unlink()
    result = runner.invoke(app, ["build", str(tmp_path / "empty-paper")])
    assert result.exit_code == 2
    assert "paper.qmd not found" in result.output
