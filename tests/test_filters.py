from __future__ import annotations

import subprocess

import pytest

from galley import template

from .conftest import FIXTURES, ROOT

FILTERS = ROOT / template.EXTENSION_DIR / "filters"

pytestmark = pytest.mark.quarto


def run_filter(filter_name: str, fixture: str, to: str = "latex") -> str:
    result = subprocess.run(
        [
            "quarto",
            "pandoc",
            str(FIXTURES / fixture),
            "--from=markdown",
            f"--to={to}",
            f"--lua-filter={FILTERS / filter_name}",
            f"--metadata-file={FIXTURES / 'filters-meta.yaml'}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_divs_become_environments() -> None:
    out = run_filter("galley-divs.lua", "divs.md")
    assert "\\begin{keyfinding}\n\nBuffers hold.\n\n\\end{keyfinding}" in out
    assert "\\begin{recommendation}[Next step]" in out
    assert "\\begin{recommendation}\n" in out
    assert "\\begin{wppanel}{A1}" in out


def test_unmapped_div_is_left_alone() -> None:
    out = run_filter("galley-divs.lua", "divs.md")
    assert "Left alone." in out
    assert "unmapped" not in out


def test_divs_untouched_for_other_formats() -> None:
    out = run_filter("galley-divs.lua", "divs.md", to="html")
    assert '<div class="keyfinding">' in out
    assert "\\begin" not in out


def test_table_gets_configured_font_size() -> None:
    out = run_filter("galley-tables.lua", "table.md")
    assert out.index("\\begingroup\\small") < out.index("\\begin{longtable}")
    assert out.index("\\end{longtable}") < out.index("\\endgroup")
    assert "\\toprule" in out


def test_caption_row_end_is_joined() -> None:
    out = run_filter("galley-latex.lua", "caption-row-end.md")
    assert "\\caption{Outflow rates}\\tabularnewline" in out
    # A row end that does not follow a caption stays where it is.
    assert "Ordinary paragraph.\n\n\\tabularnewline" in out


def test_review_filter_is_inert() -> None:
    assert run_filter("galley-review.lua", "table.md") == run_filter("galley-latex.lua", "table.md")
