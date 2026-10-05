"""Builders for test documents: a Word source and the paper it converts to."""

from __future__ import annotations

from pathlib import Path

from galley import manifest, scaffold
from galley.demo.sample import (
    BULLETS,
    CHART_CAPTION,
    CHART_CATEGORIES,
    CHART_NS,
    CHART_REL,
    CHART_SERIES,
    CHART_TYPE,
    CLOSING,
    FIGURE_CAPTION,
    INTRO,
    KEY_FINDING,
    RESULTS,
    SCOPE,
    SENSITIVITY,
    TABLE,
    TABLE_CAPTION,
    add_chart,
    chart_xml,
    make_docx,
    make_png,
)

__all__ = [
    "BULLETS",
    "CHART_CAPTION",
    "CHART_CATEGORIES",
    "CHART_NS",
    "CHART_REL",
    "CHART_SERIES",
    "CHART_TYPE",
    "CLOSING",
    "FIGURE_CAPTION",
    "INTRO",
    "KEY_FINDING",
    "PAPER_QMD",
    "RESULTS",
    "SCOPE",
    "SENSITIVITY",
    "TABLE",
    "TABLE_CAPTION",
    "add_chart",
    "chart_xml",
    "make_docx",
    "make_paper",
    "make_png",
]

PAPER_QMD = f"""\
---
title: "Deposit Outflow Review"
author:
  - Risk Analytics Team
date: "2026-10-05"
document-id: WP-TEST-002
classification: Internal
version: "0.3"
---

# Introduction

{INTRO}

{SENSITIVITY}

::: {{.keyfinding}}
{KEY_FINDING}
:::

## Scope

{SCOPE.replace("$", chr(92) + "$")}

- {BULLETS[0]}
- {BULLETS[1]}

# Results

@fig-buffer shows the buffer and @tbl-rates lists the outflow rates.

![{FIGURE_CAPTION}](figures/source/image1.png){{#fig-buffer width=70%}}

| Category           | Rate | Balance |
|:-------------------|-----:|--------:|
| Stable retail      |   5% |  12,400 |
| Less stable retail |  10% |   6,150 |
| Operational        |  25% |   6,255 |

: {TABLE_CAPTION} {{#tbl-rates}}

{CLOSING}
"""


def make_paper(paper_dir: Path, qmd: str = PAPER_QMD) -> Path:
    """A paper repo holding the hand-converted equivalent of :func:`make_docx`."""
    paper_dir.mkdir(parents=True, exist_ok=True)
    scaffold.vendor_extension(paper_dir)
    scaffold.write_project_files(paper_dir, paper_dir.name)
    manifest.save(paper_dir, [])
    make_png(paper_dir / "figures" / "source" / "image1.png")
    (paper_dir / "paper.qmd").write_text(qmd, encoding="utf-8")
    return paper_dir
