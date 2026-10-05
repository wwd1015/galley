from __future__ import annotations

import pandas as pd
import pytest

from galley import tables
from galley.config import PaperConfig

CONFIG = PaperConfig(tables={"font-size": "small"})
PLAIN = PaperConfig()
DF = pd.DataFrame(
    {"Category": ["Stable retail", "R&D"], "Rate": [0.05, 0.4], "Balance": [12400, -6150]}
)


def test_bare_tabular_for_quarto_chunks() -> None:
    out = tables.to_latex(DF, config=PLAIN)
    assert out.startswith("\\begin{tabular}{@{}lrr@{}}\n\\toprule\n")
    assert "Category & Rate & Balance \\\\\n\\midrule" in out
    assert out.rstrip().endswith("\\bottomrule\n\\end{tabular}")
    assert "longtable" not in out


def test_formats_and_escaping() -> None:
    out = tables.to_latex(DF, formats={"Rate": "percent:0", "Balance": "number"}, config=PLAIN)
    assert "Stable retail & 5\\% & 12,400 \\\\" in out
    assert "R\\&D & 40\\% & -6,150 \\\\" in out


def test_font_size_from_config_and_override() -> None:
    assert tables.to_latex(DF, config=CONFIG).startswith("\\begingroup\\small\n")
    assert tables.to_latex(DF, config=CONFIG).rstrip().endswith("\\endgroup")
    assert tables.to_latex(DF, config=CONFIG, font_size="footnotesize").startswith(
        "\\begingroup\\footnotesize\n"
    )
    assert not tables.to_latex(DF, config=CONFIG, font_size="").startswith("\\begingroup")


def test_caption_gives_standalone_longtable() -> None:
    out = tables.to_latex(DF, caption="Outflows, 5% min", label="tbl-out", config=PLAIN)
    assert "\\begin{longtable}{@{}lrr@{}}" in out
    assert "\\caption{Outflows, 5\\% min}\\label{tbl-out}\\\\" in out
    assert "\\endlastfoot" in out


def test_string_columns_that_look_numeric_are_right_aligned() -> None:
    df = pd.DataFrame({"Name": ["a", "b"], "Rate": ["5%", "10%"], "Bal": ["12,400", "(6,150)"]})
    assert "{@{}lrr@{}}" in tables.to_latex(df, config=PLAIN)
    assert "5\\% & 12,400" in tables.to_latex(df, config=PLAIN)


def test_callable_formatter_and_missing_values() -> None:
    df = pd.DataFrame({"x": [1.0, None]})
    out = tables.to_latex(df, formats={"x": lambda v: f"<{v:.0f}>"}, config=PLAIN)
    assert "<1> \\\\" in out
    assert "\n \\\\\n" in out


def test_bad_arguments() -> None:
    with pytest.raises(KeyError, match="unknown column"):
        tables.to_latex(DF, formats={"Nope": "number"}, config=PLAIN)
    with pytest.raises(ValueError, match="align has 2 columns"):
        tables.to_latex(DF, align="lr", config=PLAIN)
