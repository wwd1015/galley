from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest

from galley import fidelity
from galley.fidelity import Thresholds
from galley.template import TemplateConfig

from .conftest import ROOT


def make_pdf(path: Path, lines: list[str], pages: int = 1) -> Path:
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page(width=300, height=200)
        for row, line in enumerate(lines):
            page.insert_text((20, 40 + 20 * row), line, fontsize=12)
    document.save(path)
    return path


def test_page_diff_identical_is_zero() -> None:
    image = np.full((10, 10), 255, dtype=np.uint8)
    assert fidelity.page_diff(image, image.copy(), 32) == 0.0


def test_page_diff_counts_pixels_beyond_tolerance() -> None:
    reference = np.full((10, 10), 255, dtype=np.uint8)
    candidate = reference.copy()
    candidate[0, :5] = 0  # five clearly different pixels
    candidate[1, :5] = 240  # within tolerance
    assert fidelity.page_diff(candidate, reference, 32) == pytest.approx(0.05)


def test_page_diff_size_mismatch_is_total() -> None:
    a = np.zeros((10, 10), dtype=np.uint8)
    b = np.zeros((10, 12), dtype=np.uint8)
    assert fidelity.page_diff(a, b, 32) == 1.0


def test_compare_identical_pdfs_passes(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf", ["Outflow rate 40%"])
    b = make_pdf(tmp_path / "b.pdf", ["Outflow rate 40%"])
    result = fidelity.compare_pdfs(a, b, Thresholds(), tmp_path / "diff")
    assert result.passed
    assert result.page_diffs == (0.0,)
    assert result.diff_images == ()


def test_compare_catches_one_changed_digit(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf", ["Outflow rate 40%"])
    b = make_pdf(tmp_path / "b.pdf", ["Outflow rate 48%"])
    result = fidelity.compare_pdfs(a, b, Thresholds(), tmp_path / "diff")
    assert not result.passed
    assert result.diff_images == (tmp_path / "diff" / "page-1-diff.png",)
    assert result.diff_images[0].is_file()
    assert "FAIL" in result.summary()


def test_compare_fails_on_page_count(tmp_path: Path) -> None:
    a = make_pdf(tmp_path / "a.pdf", ["text"], pages=2)
    b = make_pdf(tmp_path / "b.pdf", ["text"], pages=1)
    result = fidelity.compare_pdfs(a, b, Thresholds())
    assert not result.passed
    assert (result.candidate_pages, result.reference_pages) == (2, 1)


def test_load_thresholds_reads_file_and_defaults(tmp_path: Path) -> None:
    assert fidelity.load_thresholds(tmp_path / "missing.yaml") == Thresholds()
    path = tmp_path / "fidelity.yaml"
    path.write_text("dpi: 150\nmax-page-diff: 0.002\n", encoding="utf-8")
    assert fidelity.load_thresholds(path) == Thresholds(dpi=150, max_page_diff=0.002)


@pytest.mark.tex
def test_golden_document_matches_reference(config: TemplateConfig, tmp_path: Path) -> None:
    """Phase 1 acceptance: Galley's pages match the original class's pages."""
    candidate = fidelity.render_golden(ROOT, config, tmp_path / "golden")
    reference = ROOT / fidelity.GOLDEN_DIR / fidelity.REFERENCE_PDF
    thresholds = fidelity.load_thresholds(ROOT / "config" / "fidelity.yaml")
    result = fidelity.compare_pdfs(candidate, reference, thresholds, tmp_path / "diff")
    assert result.passed, result.summary()


@pytest.mark.tex
def test_reference_pdf_is_current(config: TemplateConfig, tmp_path: Path) -> None:
    """The committed reference.pdf is what reference.tex and template/ produce."""
    rebuilt = fidelity.build_reference(ROOT, config, tmp_path / "reference")
    committed = ROOT / fidelity.GOLDEN_DIR / fidelity.REFERENCE_PDF
    thresholds = fidelity.load_thresholds(ROOT / "config" / "fidelity.yaml")
    result = fidelity.compare_pdfs(rebuilt, committed, thresholds, tmp_path / "diff")
    assert result.passed, result.summary()
