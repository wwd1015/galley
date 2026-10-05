from __future__ import annotations

from pathlib import Path

import pytest

from galley.verify import candidate as candidate_module
from galley.verify import checks
from galley.verify.checks import CheckResult, Finding
from galley.verify.extract_docx import extract_docx
from galley.verify.extract_pdf import extract_pdf
from galley.verify.model import DocModel, Heading, Section
from galley.verify.normalize import (
    heading_key,
    join_lines,
    number_counts,
    numbers,
    strip_heading_number,
    strip_labels,
    tokens,
)
from galley.verify.report import apply_acceptances, overall_status, render_markdown
from galley.verify.settings import VerifySettings, load_settings

from . import builders
from .conftest import ROOT

SETTINGS = VerifySettings()


def values(text: str) -> list[str]:
    return [value for value, _ in numbers(text, SETTINGS.labels)]


def test_numbers_units_and_separators() -> None:
    assert values("5%, 10 % and 25 bps, then 12,400 and 3.50") == [
        "5%", "10%", "25bp", "12400", "3.50",
    ]  # fmt: skip


def test_numbers_currency_sign_and_ranges() -> None:
    assert values(f"$1.2m and -6,150 on pp. 401{chr(0x2013)}419, minus {chr(0x2212)}3") == [
        "$1.2", "-6150", "401", "419", "-3",
    ]  # fmt: skip


def test_numbers_glued_to_letters_and_labels() -> None:
    assert values("WP-2026-014 in Q3; see Figure 12 and Table 3.1") == ["2026", "014", "3"]


def test_tokens_normalise_ligatures_quotes_and_hyphens() -> None:
    ligature, left, right, apostrophe = chr(0xFB01), chr(0x201C), chr(0x201D), chr(0x2019)
    text = f"The run-off of {ligature}nancial {left}assets{right} isn{apostrophe}t small."
    assert tokens(text) == [
        "the", "runoff", "of", "financial", "assets", "isn't", "small",
    ]  # fmt: skip


def test_join_lines_undoes_hyphenation() -> None:
    assert join_lines(["The liquid-", "ity buffer", "", "holds."]) == "The liquidity buffer holds."


def test_heading_numbers_and_labels() -> None:
    assert strip_heading_number("1.2 Scope") == "Scope"
    assert strip_heading_number("2026 results") == "2026 results"
    assert heading_key("3. Results &  Findings") == "results findings"
    assert strip_labels("see Figure 3 and table 2.1", ["Figure", "Table"]) == "see Figure and table"


def model(*sections: tuple[str | None, str], **kwargs: object) -> DocModel:
    built = [
        Section(Heading(1, heading) if heading else None, text, location=f"sec {i}")
        for i, (heading, text) in enumerate(sections, start=1)
    ]
    return DocModel(sections=built, **kwargs)  # type: ignore[arg-type]


BODY = "Retail balances fell by 4.5% while wholesale balances fell by 12.25% over thirty days."
OTHER = "Wholesale funding is more sensitive to rating actions than retail funding and reprices."


def test_text_passes_when_everything_is_present() -> None:
    source = model(("Intro", BODY), ("Scope", OTHER))
    result = checks.check_text(source, model(("Intro", BODY), ("Scope", OTHER)), SETTINGS)
    assert result.status == "pass"
    assert "100.00%" in result.summary


def test_text_catches_dropped_paragraph_and_locates_it() -> None:
    source = model(("Intro", BODY + " " + OTHER), ("Scope", "Three portfolios."))
    result = checks.check_text(
        source, model(("Intro", BODY), ("Scope", "Three portfolios.")), SETTINGS
    )
    assert result.status == "fail"
    run = next(f for f in result.findings if f.kind == "missing-text")
    assert run.location == "sec 1"
    assert "[wholesale funding is more sensitive" in run.source
    assert any(f.id == "text-coverage" for f in result.findings)


def test_text_tolerates_moved_text() -> None:
    footnote = "The survival horizon is thirty calendar days in every scenario."
    source = model(("Intro", BODY + " " + footnote), ("Scope", OTHER))
    moved = model(("Intro", BODY), ("Scope", OTHER + " " + footnote))
    assert checks.check_text(source, moved, SETTINGS).status == "pass"


def test_numbers_catches_changed_number() -> None:
    result = checks.check_numbers(
        model(("Intro", BODY)), model(("Intro", BODY.replace("12.25", "12.52"))), SETTINGS
    )
    assert result.status == "fail"
    assert {f.id for f in result.findings} == {"numbers-missing-12.25%", "numbers-extra-12.52%"}
    missing = result.findings[0]
    assert missing.numeric and "12.25%" in missing.source and missing.location == "sec 1"


def test_numbers_ignore_front_matter_and_renumbered_labels() -> None:
    source = model(("Intro", "See Figure 2. Rates rose 5%."))
    built = model(
        (None, "WP-TEST-002 version 0.3"),
        ("Intro", "See Figure 1. Rates rose 5%."),
        metadata=["WP-TEST-002", "0.3"],
    )
    assert checks.check_numbers(source, built, SETTINGS).status == "pass"


def test_numbers_extra_can_be_downgraded_to_warning() -> None:
    lenient = VerifySettings(extra_numbers_fail=False)
    result = checks.check_numbers(model(("A", "5%")), model(("A", "5% and 7")), lenient)
    assert result.status == "warn"


def test_structure_catches_renamed_missing_and_relevelled() -> None:
    source = DocModel(
        sections=[
            Section(Heading(1, "Intro")),
            Section(Heading(2, "Scope")),
            Section(Heading(1, "Results")),
        ]
    )
    renamed = DocModel(
        sections=[
            Section(Heading(1, "Intro")),
            Section(Heading(2, "Coverage")),
            Section(Heading(1, "Results")),
        ]
    )
    result = checks.check_structure(source, renamed)
    assert result.status == "fail"
    assert (result.findings[0].source, result.findings[0].candidate) == ("## Scope", "## Coverage")
    # Levels are compared relative to the top level, and numbering is ignored.
    shifted = DocModel(
        sections=[
            Section(Heading(2, "1 Intro")),
            Section(Heading(3, "1.1 Scope")),
            Section(Heading(2, "2 Results")),
        ]
    )
    assert checks.check_structure(source, shifted).status == "pass"


def test_exhibits_counts_and_captions() -> None:
    source = DocModel(
        figures=1,
        tables=1,
        figure_captions=["Liquidity buffer over the horizon"],
        table_captions=["Outflow rates by category"],
    )
    same = DocModel(
        figures=1,
        tables=1,
        figure_captions=["Liquidity buffer over the horizon"],
        table_captions=["Outflow rates by category"],
    )
    assert checks.check_exhibits(source, same, SETTINGS).status == "pass"
    lost = DocModel(figures=0, tables=1, table_captions=["Outflow rates by segment"])
    result = checks.check_exhibits(source, lost, SETTINGS)
    assert {f.kind for f in result.findings} == {"figure-count", "figure-caption", "table-caption"}


def test_notes_counts_and_unresolved_citations() -> None:
    source = DocModel(footnotes=["one", "two"])
    result = checks.check_notes(
        source, DocModel(footnotes=["one"], unresolved_citations=["TODO-1"])
    )
    assert result.status == "fail"
    assert [f.kind for f in result.findings] == ["footnote-count", "unresolved-citation"]
    listed = checks.check_notes(
        source, DocModel(footnotes=["a", "b"], unresolved_citations=["TODO-1"])
    )
    assert listed.status == "warn"
    assert (
        "could not be identified" in checks.check_notes(DocModel(), DocModel(footnotes=[])).summary
    )


def failing_checks() -> list[CheckResult]:
    return [
        CheckResult(
            "text", "Text coverage", True, findings=[Finding("text-abc", "missing-text", "m")]
        ),
        CheckResult(
            "numbers",
            "Numbers",
            True,
            findings=[Finding("numbers-missing-5%", "missing-number", "m", numeric=True)],
        ),
    ]


def write_accept(tmp_path: Path, body: str) -> None:
    (tmp_path / "verify-accept.yaml").write_text(body, encoding="utf-8")


def test_acceptance_of_text_difference(tmp_path: Path) -> None:
    results = failing_checks()
    write_accept(tmp_path, "- id: text-abc\n  reason: formatting only\n")
    assert apply_acceptances(results, tmp_path) == []
    assert results[0].status == "pass" and results[1].status == "fail"
    assert overall_status(results) == "fail"


@pytest.mark.parametrize("approver", ["", "Claude", "galley-verify", "AI assistant"])
def test_numeric_mismatch_cannot_be_accepted_by_a_tool(tmp_path: Path, approver: str) -> None:
    results = failing_checks()
    write_accept(tmp_path, f"- id: numbers-missing-5%\n  reason: ok\n  approved-by: '{approver}'\n")
    rejected = apply_acceptances(results, tmp_path)
    assert rejected and "naming a person" in rejected[0]
    assert results[1].status == "fail"


def test_numeric_mismatch_accepted_by_a_named_person(tmp_path: Path) -> None:
    results = failing_checks()
    write_accept(
        tmp_path,
        "- id: numbers-missing-5%\n  reason: source typo\n  approved-by: Wendi Wang\n"
        "- id: text-abc\n  reason: formatting\n",
    )
    assert apply_acceptances(results, tmp_path) == []
    assert overall_status(results) == "pass"
    assert "approved by Wendi Wang" in results[1].findings[0].accepted_reason


def test_markdown_report_lists_findings() -> None:
    results = failing_checks()
    report = {
        "status": "fail",
        "source": "a.docx",
        "pdf": "paper.pdf",
        "generated-at": "now",
        "checks": [c.as_dict() for c in results],
        "rejected-acceptances": [],
        "visual": [],
    }
    text = render_markdown(report)
    assert "**FAIL**" in text and "| `numbers-missing-5%` |" in text
    assert "| Numbers | FAIL |" in text


def test_extract_docx(tmp_path: Path) -> None:
    source = builders.make_docx(tmp_path / "source.docx", builders.make_png(tmp_path / "i.png"))
    doc = extract_docx(source, SETTINGS)
    assert [(h.level, h.text) for h in doc.headings] == [
        (1, "Introduction"), (2, "Scope"), (1, "Results"),
    ]  # fmt: skip
    assert (doc.figures, doc.tables) == (1, 1)
    assert doc.figure_captions == [builders.FIGURE_CAPTION]
    assert doc.table_captions == [builders.TABLE_CAPTION]
    assert doc.footnotes == []
    assert "Stable retail 5% 12,400" in doc.text
    assert number_counts(doc.text, SETTINGS.labels)["12.25%"] == 1


def test_extract_pdf_from_typeset_reference() -> None:
    doc = extract_pdf(ROOT / "tests" / "golden" / "reference.pdf", SETTINGS)
    assert [h.text for h in doc.headings][:3] == ["Introduction", "Scope", "Scenario design"]
    assert doc.figure_captions == ["Cumulative liquidity buffer over the stress horizon"]
    assert doc.table_captions == ["Outflow assumptions by deposit category"]
    assert doc.footnotes is not None and len(doc.footnotes) == 1
    text = doc.text
    assert "CONFIDENTIAL" not in text  # running header removed
    assert "Stable retail" in text and "12,400" in text
    intro = next(s for s in doc.sections if s.heading and s.heading.text == "Introduction")
    assert intro.text.startswith("Liquidity stress testing follows")


def test_tex_headings_and_footnotes() -> None:
    tex = (
        "\\title{Ignored}\\begin{document}\\section{Results \\& \\emph{Findings}}\\label{r}"
        "Text\\footnote{One} \\subsection*{Scope}\\paragraph{Market stress}\\footnote{Two}"
    )
    assert [(h.level, h.text) for h in candidate_module.tex_headings(tex)] == [
        (1, "Results & Findings"), (2, "Scope"), (4, "Market stress"),
    ]  # fmt: skip
    assert candidate_module.tex_footnote_count(tex) == 2


def test_load_settings_defaults_and_overrides(tmp_path: Path) -> None:
    assert load_settings().min_coverage == 0.995
    path = tmp_path / "verify.yaml"
    path.write_text("text:\n  max-missing-run: 3\nnumbers:\n  extra: warn\n", encoding="utf-8")
    custom = load_settings(path)
    assert custom.max_missing_run == 3 and custom.extra_numbers_fail is False
