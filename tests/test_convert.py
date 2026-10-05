from __future__ import annotations

import csv
import stat
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from galley import convert as converter
from galley import manifest
from galley.cli import app
from galley.convert import ast, citations
from galley.convert.docx import _label_pattern, parse_chart, strip_caption_label
from galley.convert.model import Conversion, render_report

from . import builders
from .conftest import ROOT

BIB = ROOT / "template" / "references.bib"


def test_parse_bib_and_match() -> None:
    entries = citations.parse_bib(BIB)
    assert {e.key for e in entries} == {"bcbs2013", "diamond1983"}
    assert citations.match("Basel Committee", "2013", entries) == "bcbs2013"
    assert citations.match("Diamond and Dybvig", "1983", entries) == "diamond1983"
    assert citations.match("Diamond", "1984", entries) is None
    assert citations.match("Unknown", "2013", entries) is None


def test_convert_citations_matches_flags_and_skips_code() -> None:
    text = (
        "---\ntitle: A (Smith 2020) title\n---\n"
        "See (Basel Committee 2013; Nobody 1999) and (see Section 3).\n"
        "```\nprint('(Diamond and Dybvig 1983)')\n```\n"
        "Runs (Diamond and Dybvig, 1983).\n"
    )
    result = citations.convert_citations(text, citations.parse_bib(BIB))
    assert (
        "[@bcbs2013; @TODO-1]<!-- original: (Basel Committee 2013; Nobody 1999) -->" in result.text
    )
    assert "(see Section 3)" in result.text
    assert "print('(Diamond and Dybvig 1983)')" in result.text
    assert "Runs [@diamond1983]." in result.text
    assert "title: A (Smith 2020) title" in result.text
    assert result.unresolved == {"TODO-1": "Nobody 1999"}
    assert set(result.matched.values()) == {"bcbs2013", "diamond1983"}


def test_narrative_citations_convert_only_when_matched() -> None:
    entries = citations.parse_bib(BIB)
    text = "See Diamond and Dybvig (1983) for runs; Smith (2020) disagrees. In March (2013) too.\n"
    result = citations.convert_citations(text, entries)
    assert result.text == (
        "See @diamond1983 for runs; Smith (2020) disagrees. In March (2013) too.\n"
    )
    assert result.unresolved == {}


def test_drop_reference_list() -> None:
    body = "# Intro\n\nText.\n\n# References\n\nEntry one.\n\n## Sub\n\nx\n\n# Annex\n\nMore.\n"
    out, dropped = converter.drop_reference_list(body, ["References"])
    assert dropped and "Entry one" not in out and "# Annex" in out
    plain = "Text.[^1]\n\nReferences\n\nEntry one.\n\n[^1]: A note.\n"
    out, dropped = converter.drop_reference_list(plain, ["References"])
    assert dropped and "Entry one" not in out and "[^1]: A note." in out
    assert converter.drop_reference_list(body, ["Sources"]) == (body, False)


def test_split_sections_at_top_level_headings() -> None:
    body = "Preface.\n\n# One {#sec-one}\n\na\n\n```\n# not a heading\n```\n\n# Two & More\n\nb\n"
    sections = converter.split_sections(body)
    assert [name for name, _ in sections] == ["01-front.qmd", "02-one.qmd", "03-two-more.qmd"]
    assert "# not a heading" in sections[1][1]


def test_strip_caption_label_keeps_formatting() -> None:
    pattern = _label_pattern(["Figure", "Fig."])
    inlines = [
        *ast.text_inlines("Figure 3."),
        ast.space(),
        {"t": "Emph", "c": ast.text_inlines("Net")},
        ast.space(),
        ast.str_("outflows"),
    ]
    rest, number = strip_caption_label(inlines, pattern)
    assert number == "3"
    assert rest[0]["t"] == "Emph" and ast.stringify(rest) == "Net outflows"
    assert strip_caption_label(ast.text_inlines("No label here"), pattern)[1] == ""


def test_parse_chart_reads_cached_series() -> None:
    xml = builders.chart_xml("Balances", builders.CHART_CATEGORIES, builders.CHART_SERIES)
    chart = parse_chart(xml, 1)
    assert chart is not None
    assert (chart.kind, chart.title) == ("line", "Balances")
    assert chart.header == ["category", "Retail", "Wholesale"]
    assert chart.rows[0] == ["Q1", "101", "96"] and len(chart.rows) == 4


def test_report_lists_everything_a_person_must_check() -> None:
    conversion = Conversion(
        needs_data=["fig-1 (figures/source/a.png)"],
        unsure=["Check table 2."],
        unresolved_citations={"TODO-1": "Nobody 1999"},
    )
    conversion.note_style("Pull Quote", "Funding costs rose")
    verify = {
        "status": "fail",
        "checks": [
            {
                "title": "Numbers",
                "status": "fail",
                "summary": "s",
                "findings": [
                    {
                        "id": "numbers-missing-5%",
                        "message": "gone",
                        "fails": True,
                        "accepted": False,
                    }
                ],
            }
        ],
    }
    text = render_report(
        conversion,
        source_name="original.docx",
        source_url="https://x",
        verify_report=verify,
        verify_error="",
    )
    for expected in (
        "| Pull Quote | 1 | Funding costs rose |",
        "`fig-1 (figures/source/a.png)`",
        "`[@TODO-1]`: Nobody 1999",
        "- Check table 2.",
        "**FAIL**",
        "`numbers-missing-5%`: gone",
        "exported from https://x",
    ):
        assert expected in text


def test_convert_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(converter.ConvertError, match="unsupported input"):
        converter.convert(tmp_path / "a.txt", tmp_path / "out")
    with pytest.raises(converter.ConvertError, match="not found"):
        converter.convert(tmp_path / "a.docx", tmp_path / "out")
    with pytest.raises(converter.ConvertError, match="not a Google Docs URL"):
        converter.export_google_doc("https://example.com/doc", tmp_path / "x.docx")


@pytest.fixture(scope="module")
def sources(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("sources")
    image = builders.make_png(base / "image.png")
    builders.make_docx(base / "simple.docx", image)
    builders.make_docx(base / "rich.docx", image, references=True, chart=True, extras=True)
    return base


@pytest.mark.quarto
def test_docx_structure_without_verify(sources: Path, tmp_path: Path) -> None:
    out = tmp_path / "paper"
    conversion, report = converter.convert(
        sources / "rich.docx",
        out,
        bib=BIB,
        source_url="https://docs.google.com/document/d/abc",
        run_verify=False,
    )
    assert report is None
    text = (out / "paper.qmd").read_text(encoding="utf-8")
    matter = yaml.safe_load(text.split("---\n")[1])
    assert matter["title"] == "Deposit Outflow Review"
    assert matter["galley-source"] == {
        "source": "source/original.docx", "source-url": "https://docs.google.com/document/d/abc",
    }  # fmt: skip
    assert matter["bibliography"] == "references.bib"

    # Styles: mapped to a div, ignored silently, or reported.
    assert "::: keyfinding\nThe buffer covers 118%" in text
    assert "custom-style" not in text
    assert dict(conversion.unmapped_styles) == {"Pull Quote": 1}
    assert "Funding costs rose over the year." in text  # kept, never dropped

    # Exhibits: image marked needs-data, table as CSV, chart rebuilt from embedded data.
    assert (
        "![Liquidity buffer over the stress horizon](figures/source/image1.png)"
        '{#fig-1 width="4.0in" galley-status="needs-data"}'
    ) in text
    assert (out / "figures" / "source" / "image1.png").is_file()
    assert '#| label: tbl-1\n#| tbl-cap: "Outflow rates by category"\n#| output: asis' in text
    with (out / "data" / "tables" / "tbl-1.csv").open(encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == builders.TABLE
    assert '#| label: fig-2\n#| fig-cap: "Deposit balances by quarter"' in text
    assert (
        (out / "data" / "charts" / "chart-1.csv")
        .read_text("utf-8")
        .startswith("category,Retail,Wholesale\nQ1,101,96\n")
    )
    assert "def chart_1(df: pd.DataFrame) -> Figure:" in (
        out / "py" / "exhibits" / "converted.py"
    ).read_text(encoding="utf-8")
    assert "from exhibits.converted import chart_1" in text

    # Cross-references and citations.
    assert "@fig-1 shows the buffer and @tbl-1 lists the outflow rates." in text
    assert "@fig-2 compares retail and wholesale balances." in text
    assert "[@bcbs2013]" in text and "[@diamond1983]" in text
    assert "[@TODO-1]<!-- original: (Unknown 1999) -->" in text
    assert conversion.unresolved_citations == {"TODO-1": "Unknown 1999"}

    # The original is kept read-only and all data is pinned.
    original = out / "source" / "original.docx"
    assert original.read_bytes() == (sources / "rich.docx").read_bytes()
    assert not original.stat().st_mode & stat.S_IWUSR
    assert manifest.verify(out) == []
    report_text = (out / "conversion-report.md").read_text(encoding="utf-8")
    assert "| Pull Quote | 1 |" in report_text and "Not run (`--no-verify`)" in report_text


@pytest.mark.quarto
def test_long_paper_is_split_into_sections(sources: Path, tmp_path: Path) -> None:
    style_map = tmp_path / "style-map.yaml"
    style_map.write_text("split-sections-over-words: 20\nignore: [List Bullet, Caption]\n", "utf-8")
    out = tmp_path / "paper"
    converter.convert(sources / "simple.docx", out, style_map_path=style_map, run_verify=False)
    assert sorted(p.name for p in (out / "sections").glob("*.qmd")) == [
        "01-introduction.qmd", "02-results.qmd",
    ]  # fmt: skip
    paper = (out / "paper.qmd").read_text(encoding="utf-8")
    assert "{{< include sections/01-introduction.qmd >}}" in paper
    assert "# Introduction" not in paper


def failing(report: dict[str, object]) -> list[str]:
    checks = report["checks"]
    assert isinstance(checks, list)
    return [
        f["id"]
        for c in checks
        if c["status"] == "fail"
        for f in c["findings"]
        if f["fails"] and not f["accepted"]
    ]


@pytest.mark.tex
def test_legacy_docx_converts_and_verifies(sources: Path, tmp_path: Path) -> None:
    """Phase 4 acceptance, paper 1: a Word whitepaper converts with verify passing."""
    _, report = converter.convert(sources / "simple.docx", tmp_path / "paper")
    assert report is not None
    assert report["status"] == "pass", (tmp_path / "paper" / "verify-report.md").read_text("utf-8")
    assert "**PASS**" in (tmp_path / "paper" / "conversion-report.md").read_text("utf-8")


@pytest.mark.tex
def test_legacy_docx_with_chart_explains_every_failure(sources: Path, tmp_path: Path) -> None:
    """Phase 4 acceptance, paper 2: failures are all explained in the conversion report."""
    out = tmp_path / "paper"
    conversion, report = converter.convert(sources / "rich.docx", out, bib=BIB)
    assert report is not None
    statuses = {c["id"]: c["status"] for c in report["checks"]}
    assert statuses["structure"] == "pass" and statuses["exhibits"] == "pass"
    # The only content lost is the citation that could not be matched.
    assert set(failing(report)) <= {"text-coverage", "numbers-missing-1999"}
    text = (out / "conversion-report.md").read_text(encoding="utf-8")
    assert "`[@TODO-1]`: Unknown 1999" in text
    for finding in failing(report):
        assert f"`{finding}`" in text
    assert len(conversion.charts) == 1


@pytest.mark.tex
def test_legacy_pdf_converts_with_failures_explained(tmp_path: Path) -> None:
    """Phase 4 acceptance, paper 3: a typeset PDF converts; what is lost is reported."""
    out = tmp_path / "paper"
    source = ROOT / "tests" / "golden" / "reference.pdf"
    conversion, report = converter.convert(source, out, bib=BIB)
    assert report is not None
    text = (out / "paper.qmd").read_text(encoding="utf-8")
    assert "# Introduction" in text and "### Scenario design" in text
    assert 'figures/source/fig-1.png){#fig-1 galley-status="needs-data"}' in text
    assert "- committed credit and liquidity facilities;" in text
    assert "a footnote.[^1]" in text and "[^1]: The survival horizon is 30 calendar days" in text
    with (out / "data" / "tables" / "tbl-1.csv").open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["Category", "Outflow rate", "Balance ($m)"]
    assert rows[1] == ["Stable retail", "5%", "12,400"]

    statuses = {c["id"]: c["status"] for c in report["checks"]}
    assert statuses["structure"] == "pass" and statuses["exhibits"] == "pass"
    assert statuses["notes"] == "pass"
    # What remains is the title page, which the report tells a person to map by hand.
    report_text = (out / "conversion-report.md").read_text(encoding="utf-8")
    assert "Title-page text was not carried into the body" in report_text
    assert "WP-TEST-001" in report_text
    for finding in failing(report):
        assert f"`{finding}`" in report_text
    assert all(
        f.startswith(("text-", "numbers-missing-0.9", "numbers-missing-001"))
        for f in failing(report)
    ), failing(report)
    assert conversion.needs_data


@pytest.mark.tex
def test_cli_convert(sources: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["convert", str(sources / "simple.docx"), "--out", str(tmp_path / "paper")]
    )
    assert result.exit_code == 0, result.output
    assert "verify: PASS" in result.output
    again = runner.invoke(
        app, ["convert", str(sources / "simple.docx"), "--out", str(tmp_path / "paper")]
    )
    assert again.exit_code == 2 and "not empty" in again.output


def test_rows_from_words_rebuilds_columns() -> None:
    from galley.convert.pdf import rows_from_words

    words: list[tuple[float, float, float, float, str]] = [
        (100, 10, 140, 20, "Category"), (200, 10, 232, 20, "Outflow"), (235, 10, 255, 20, "rate"),
        (100, 30, 125, 40, "Stable"), (128, 30, 150, 40, "retail"), (240, 30, 255, 40, "5%"),
        (100, 50, 160, 60, "Operational"), (236, 50, 255, 60, "25%"),
    ]  # fmt: skip
    assert rows_from_words(words) == [
        ["Category", "Outflow rate"], ["Stable retail", "5%"], ["Operational", "25%"],
    ]  # fmt: skip
