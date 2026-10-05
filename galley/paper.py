"""Helpers called from code chunks inside a paper."""

from __future__ import annotations

import sys
from pathlib import Path

from galley import style
from galley.config import find_paper_root


def root() -> Path:
    """The paper repo root (the directory that vendors the galley-pdf extension)."""
    found = find_paper_root()
    if found is None:
        raise RuntimeError("not inside a Galley paper (no vendored galley-pdf extension found)")
    return found


def setup(pgf: bool | None = None) -> None:
    """Make ``py/`` importable and apply the chart style. Call once per document.

    Returns nothing, so that a chunk ending in ``paper.setup()`` prints nothing.
    """
    paper_root = root()
    exhibits = str(paper_root / "py")
    if exhibits not in sys.path:
        sys.path.insert(0, exhibits)
    style.use(pgf=pgf)
