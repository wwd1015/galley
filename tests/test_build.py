from __future__ import annotations

import json
import shutil
from pathlib import Path

import pymupdf
import pytest

from galley import build, scaffold
from galley.config import load_paper_config

OUTPUTS = ("paper.pdf", "paper.tex", "paper_files", "build-report.json")


def test_unresolved_references() -> None:
    tex = (
        "\\section{A}\\label{sec-a} See \\ref{sec-a}, \\ref{fig-gone} and \\textbf{?@tbl-missing}."
    )
    assert build.unresolved_references(tex) == ["@tbl-missing", "fig-gone"]


def test_undefined_citations(tmp_path: Path) -> None:
    bib = tmp_path / "references.bib"
    bib.write_text("@article{known2020,\n title={x}}\n@book{ other1999 ,\n}\n", encoding="utf-8")
    tex = "\\citep{known2020, ghost2021} \\citet[p.~3]{other1999} \\autocite{TODO-1}"
    assert build.undefined_citations(tex, [bib, tmp_path / "absent.bib"]) == [
        "TODO-1",
        "ghost2021",
    ]


def test_figures_needing_data(tmp_path: Path) -> None:
    doc = tmp_path / "paper.qmd"
    doc.write_text(
        'text\n![Cap](figures/source/a.png){#fig-a galley-status="needs-data"}\n'
        "```{python}\n#| galley-status: needs-data\n```\n",
        encoding="utf-8",
    )
    assert build.figures_needing_data([doc]) == ["paper.qmd:2", "paper.qmd:4"]


def test_main_document_from_project(tmp_path: Path) -> None:
    assert build.main_document(tmp_path) == "paper.qmd"
    (tmp_path / "_quarto.yml").write_text("project:\n  render:\n    - report.qmd\n", "utf-8")
    assert build.main_document(tmp_path) == "report.qmd"


@pytest.fixture(scope="module")
def built_paper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    paper = scaffold.new_paper("sample-paper", tmp_path_factory.mktemp("build"), git=False)
    result = build.build(paper)
    assert result.ok, result.problems
    return paper


@pytest.mark.tex
def test_build_writes_report_and_pdf(built_paper: Path) -> None:
    report = json.loads((built_paper / "build-report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["output"] == {"pdf": "paper.pdf", "pages": 2}
    assert list(report["data"]) == ["data/example.csv"]
    assert report["tools"]["quarto"] and report["tools"]["texlive"]
    assert report["checks"]["unresolved-references"] == []
    assert (built_paper / "_freeze").is_dir()


@pytest.mark.tex
def test_charts_use_the_template_font(built_paper: Path) -> None:
    """Phase 2 acceptance: chart text is set in the template's font."""
    required = str(load_paper_config(built_paper).style["font-check"])
    charts = sorted(built_paper.glob("paper_files/figure-pdf/*.pdf"))
    assert charts, "the sample paper produced no chart"
    for chart in charts:
        with pymupdf.open(chart) as pdf:
            fonts = {font[3] for page in pdf for font in page.get_fonts()}
        assert any(required in name for name in fonts), fonts
    assert build.chart_fonts(built_paper, "paper.qmd", required) == []
    assert build.chart_fonts(built_paper, "paper.qmd", "Comic Sans") != []


@pytest.mark.tex
def test_clean_checkout_reproduces_report(built_paper: Path, tmp_path: Path) -> None:
    """Phase 2 acceptance: build-report.json is byte-identical except its timestamp."""
    checkout = tmp_path / "checkout"
    shutil.copytree(built_paper, checkout, ignore=shutil.ignore_patterns(*OUTPUTS))
    result = build.build(checkout)
    assert result.ok, result.problems

    def stable(path: Path) -> str:
        report = json.loads(path.read_text(encoding="utf-8"))
        del report["generated-at"]
        return json.dumps(report, indent=2, sort_keys=True)

    assert stable(checkout / "build-report.json") == stable(built_paper / "build-report.json")


@pytest.mark.tex
def test_build_fails_on_tampered_data(built_paper: Path, tmp_path: Path) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(built_paper, tampered, ignore=shutil.ignore_patterns(*OUTPUTS))
    with (tampered / "data" / "example.csv").open("a", encoding="utf-8") as handle:
        handle.write("2026-07,90,100\n")
    result = build.build(tampered)
    assert not result.ok
    assert result.problems == ["data: hash mismatch: data/example.csv"]
    assert result.pdf is None
    assert json.loads(result.report_path.read_text(encoding="utf-8"))["ok"] is False
