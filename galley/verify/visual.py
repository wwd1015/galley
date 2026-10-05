"""Advisory visual comparison: page images a person can put side by side."""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

import pymupdf

from galley.verify.settings import VerifySettings

VISUAL_DIR = "verify-visual"


def _figure_pages(pdf: Path, settings: VerifySettings) -> list[int]:
    words = "|".join(re.escape(label) for label in settings.figure_labels)
    pattern = re.compile(rf"^\s*(?:{words})\s*\d+", re.IGNORECASE | re.MULTILINE)
    with pymupdf.open(pdf) as document:
        return [number for number, page in enumerate(document) if pattern.search(page.get_text())]


def _render_pages(pdf: Path, pages: list[int], prefix: str, out: Path) -> list[str]:
    names: list[str] = []
    with pymupdf.open(pdf) as document:
        for number in pages:
            name = f"{prefix}-page-{number + 1}.png"
            document[number].get_pixmap(dpi=80).save(out / name)
            names.append(f"{VISUAL_DIR}/{name}")
    return names


def export(source: Path, pdf: Path, paper_dir: Path, settings: VerifySettings) -> list[str]:
    """Write images of every page with a figure, from both documents."""
    out = paper_dir / VISUAL_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    names = _render_pages(pdf, _figure_pages(pdf, settings), "galley", out)
    if source.suffix.lower() == ".pdf":
        names += _render_pages(source, _figure_pages(source, settings), "source", out)
    elif source.suffix.lower() == ".docx":
        with zipfile.ZipFile(source) as archive:
            for member in sorted(archive.namelist()):
                if member.startswith("word/media/"):
                    name = f"source-{Path(member).name}"
                    (out / name).write_bytes(archive.read(member))
                    names.append(f"{VISUAL_DIR}/{name}")
    return sorted(names)
