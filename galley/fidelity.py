"""Template fidelity: compare Galley's PDF with one typeset by the original class.

The golden document in ``tests/golden`` is rendered through the ``galley-pdf``
extension and compared, page image by page image, with ``reference.pdf``,
which is compiled from a hand-written ``reference.tex`` using the team's
unmodified files.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
import yaml
from numpy.typing import NDArray

from galley.template import EXTENSION_DIR, TemplateConfig

GOLDEN_DIR = Path("tests/golden")
GOLDEN_DOC = "golden.qmd"
REFERENCE_TEX = "reference.tex"
REFERENCE_PDF = "reference.pdf"
TINYTEX_HOMES = ("Library/TinyTeX", ".TinyTeX")

Image = NDArray[np.uint8]


class FidelityError(Exception):
    """A document could not be built for comparison."""


@dataclass(frozen=True)
class Thresholds:
    dpi: int = 100
    # Grey-level difference (0-255) below which two pixels count as equal.
    pixel_tolerance: int = 32
    # Largest share of differing pixels allowed on any page.
    max_page_diff: float = 0.0


@dataclass(frozen=True)
class FidelityResult:
    candidate_pages: int
    reference_pages: int
    page_diffs: tuple[float, ...]
    max_page_diff: float
    diff_images: tuple[Path, ...] = ()

    @property
    def passed(self) -> bool:
        return self.candidate_pages == self.reference_pages and all(
            diff <= self.max_page_diff for diff in self.page_diffs
        )

    def summary(self) -> str:
        lines = [f"pages: candidate {self.candidate_pages}, reference {self.reference_pages}"]
        for number, diff in enumerate(self.page_diffs, start=1):
            verdict = "ok" if diff <= self.max_page_diff else "DIFFERS"
            lines.append(f"page {number}: {diff:.4%} of pixels differ ({verdict})")
        lines.append("PASS" if self.passed else f"FAIL (limit {self.max_page_diff:.4%} per page)")
        return "\n".join(lines)


def load_thresholds(path: Path) -> Thresholds:
    if not path.is_file():
        return Thresholds()
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = Thresholds()
    return Thresholds(
        dpi=int(raw.get("dpi", defaults.dpi)),
        pixel_tolerance=int(raw.get("pixel-tolerance", defaults.pixel_tolerance)),
        max_page_diff=float(raw.get("max-page-diff", defaults.max_page_diff)),
    )


def rasterize(pdf: Path, dpi: int) -> list[Image]:
    """Render every page of ``pdf`` to a greyscale image."""
    pages: list[Image] = []
    with pymupdf.open(pdf) as document:
        for page in document:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            flat = np.frombuffer(pixmap.samples, dtype=np.uint8)
            pages.append(flat.reshape(pixmap.height, pixmap.width).copy())
    return pages


def page_diff(candidate: Image, reference: Image, pixel_tolerance: int) -> float:
    """Share of pixels that differ by more than ``pixel_tolerance`` grey levels."""
    if candidate.shape != reference.shape:
        return 1.0
    delta = np.abs(candidate.astype(np.int16) - reference.astype(np.int16))
    return float(np.count_nonzero(delta > pixel_tolerance)) / float(delta.size)


def _write_diff_image(candidate: Image, reference: Image, tolerance: int, path: Path) -> None:
    """Save the reference page with differing pixels marked in red."""
    rgb = np.stack([reference, reference, reference], axis=-1)
    if candidate.shape == reference.shape:
        delta = np.abs(candidate.astype(np.int16) - reference.astype(np.int16))
        rgb[delta > tolerance] = (255, 0, 0)
    height, width = reference.shape
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, width, height, rgb.tobytes(), False)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(path)


def compare_pdfs(
    candidate: Path, reference: Path, thresholds: Thresholds, diff_dir: Path | None = None
) -> FidelityResult:
    """Compare two PDFs page by page; write marked-up images of failing pages."""
    candidate_pages = rasterize(candidate, thresholds.dpi)
    reference_pages = rasterize(reference, thresholds.dpi)
    diffs: list[float] = []
    images: list[Path] = []
    for number, (cand, ref) in enumerate(
        zip(candidate_pages, reference_pages, strict=False), start=1
    ):
        diff = page_diff(cand, ref, thresholds.pixel_tolerance)
        diffs.append(diff)
        if diff > thresholds.max_page_diff and diff_dir is not None:
            image = diff_dir / f"page-{number}-diff.png"
            _write_diff_image(cand, ref, thresholds.pixel_tolerance, image)
            images.append(image)
    return FidelityResult(
        candidate_pages=len(candidate_pages),
        reference_pages=len(reference_pages),
        page_diffs=tuple(diffs),
        max_page_diff=thresholds.max_page_diff,
        diff_images=tuple(images),
    )


def find_tool(name: str) -> Path | None:
    """Locate a TeX binary on PATH or in a TinyTeX install."""
    found = shutil.which(name)
    if found:
        return Path(found)
    for home in TINYTEX_HOMES:
        for candidate in sorted((Path.home() / home / "bin").glob(f"*/{name}")):
            return candidate
    return None


def _require_tool(name: str) -> str:
    tool = find_tool(name)
    if tool is None:
        raise FidelityError(f"TeX tool not found: {name}")
    return str(tool)


def _run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
        raise FidelityError(f"`{' '.join(command)}` failed in {cwd}:\n{tail}")


def compile_tex(tex: Path, engine: str) -> Path:
    """Compile ``tex`` in place with ``engine``, running biber or bibtex when needed."""
    run_engine = [_require_tool(engine), "-interaction=nonstopmode", "-halt-on-error", tex.name]
    _run(run_engine, tex.parent)
    aux = tex.with_suffix(".aux")
    if tex.with_suffix(".bcf").is_file():
        _run([_require_tool("biber"), tex.stem], tex.parent)
    elif aux.is_file() and "\\bibdata" in aux.read_text(encoding="utf-8", errors="replace"):
        _run([_require_tool("bibtex"), tex.stem], tex.parent)
    _run(run_engine, tex.parent)
    _run(run_engine, tex.parent)
    return tex.with_suffix(".pdf")


def _stage(root: Path, workdir: Path, config: TemplateConfig) -> None:
    """Copy the golden files and the team's resources into a clean build directory."""
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(root / GOLDEN_DIR, workdir)
    for name in config.resources:
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / config.source / name, target)


def build_reference(root: Path, config: TemplateConfig, workdir: Path) -> Path:
    """Compile ``reference.tex`` against the team's unmodified files."""
    _stage(root, workdir, config)
    return compile_tex(workdir / REFERENCE_TEX, config.engine)


def render_golden(root: Path, config: TemplateConfig, workdir: Path) -> Path:
    """Render ``golden.qmd`` through the galley-pdf extension."""
    quarto = shutil.which("quarto")
    if quarto is None:
        raise FidelityError("quarto not found on PATH")
    _stage(root, workdir, config)
    # Only the extension may supply the team's files to the render.
    for name in config.resources:
        (workdir / name).unlink()
    shutil.copytree(root / EXTENSION_DIR, workdir / EXTENSION_DIR)
    _run([quarto, "render", GOLDEN_DOC, "--to", "galley-pdf"], workdir)
    return workdir / Path(GOLDEN_DOC).with_suffix(".pdf").name
