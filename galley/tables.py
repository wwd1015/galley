"""Render data frames as booktabs LaTeX in the template's table convention."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from galley.config import PaperConfig, load_paper_config
from galley.formatting import (
    Formatter,
    NumberStyle,
    is_missing,
    latex_escape,
    named_formatter,
)

_NUMERIC_TEXT = re.compile(r"^[-(]?[$€£]?\d[\d,]*(\.\d+)?\s?(%|bp|bps|m|bn|k|x)?\)?$")


def _is_numeric_column(series: pd.Series[Any]) -> bool:
    if pd.api.types.is_numeric_dtype(series):
        return True
    values = [str(v).strip() for v in series if not is_missing(v) and str(v).strip()]
    return bool(values) and all(_NUMERIC_TEXT.match(v) for v in values)


def _cell(value: Any, formatter: Formatter | None) -> str:
    if is_missing(value):
        return ""
    if formatter is not None:
        return latex_escape(formatter(value))
    return latex_escape(str(value))


def to_latex(
    df: pd.DataFrame,
    *,
    formats: Mapping[str, str | Formatter] | None = None,
    align: str | None = None,
    caption: str | None = None,
    label: str | None = None,
    font_size: str | None = None,
    config: PaperConfig | None = None,
) -> str:
    """Booktabs LaTeX for ``df``.

    Without ``caption`` the result is a bare ``tabular`` for a Quarto chunk
    that sets ``tbl-cap`` and ``output: asis``. With ``caption`` it is a
    complete ``longtable`` carrying its own caption and ``label``.

    ``formats`` maps a column to a formatter or a name understood by
    :func:`galley.formatting.named_formatter` (``"percent:1"``, ``"bp"`` ...).
    """
    config = config or load_paper_config()
    style = NumberStyle.from_config(config)
    resolved: dict[str, Formatter] = {}
    for column, spec in (formats or {}).items():
        if column not in df.columns:
            raise KeyError(f"formats refers to unknown column '{column}'")
        resolved[column] = named_formatter(spec, style) if isinstance(spec, str) else spec

    columns = [str(c) for c in df.columns]
    if align is None:
        align = "".join("r" if _is_numeric_column(df[c]) else "l" for c in df.columns)
    if len(align) != len(columns):
        raise ValueError(f"align has {len(align)} columns, the table has {len(columns)}")

    header = " & ".join(latex_escape(c) for c in columns) + r" \\"
    rows = [
        " & ".join(_cell(row[c], resolved.get(str(c))) for c in df.columns) + r" \\"
        for _, row in df.iterrows()
    ]
    size = font_size if font_size is not None else str(config.tables.get("font-size", ""))
    spec = f"@{{}}{align}@{{}}"

    if caption is None:
        lines = [rf"\begin{{tabular}}{{{spec}}}", r"\toprule", header, r"\midrule"]
        lines += [*rows, r"\bottomrule", r"\end{tabular}"]
    else:
        tag = rf"\label{{{label}}}" if label else ""
        lines = [
            rf"\begin{{longtable}}{{{spec}}}",
            rf"\caption{{{latex_escape(caption)}}}{tag}\\",
            r"\toprule",
            header,
            r"\midrule",
            r"\endhead",
            r"\bottomrule",
            r"\endlastfoot",
            *rows,
            r"\end{longtable}",
        ]
    if size:
        lines = [rf"\begingroup\{size}", *lines, r"\endgroup"]
    return "\n".join(lines) + "\n"
