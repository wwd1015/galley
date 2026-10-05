from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from galley import manifest, scaffold, template
from galley.scaffold import ScaffoldError


def test_new_paper_layout(tmp_path: Path) -> None:
    paper = scaffold.new_paper("liquidity-2026", tmp_path, git=False, today=date(2026, 10, 5))
    for name in (
        "_quarto.yml",
        "_quarto-review.yml",
        "paper.qmd",
        "references.bib",
        "data/manifest.yaml",
        "data/example.csv",
        "py/exhibits/example.py",
        ".github/workflows/build.yml",
        ".gitignore",
    ):
        assert (paper / name).is_file(), name
    assert (paper / "sections").is_dir() and (paper / "source").is_dir()
    assert (paper / template.EXTENSION_DIR / "_extension.yml").is_file()
    assert (paper / template.EXTENSION_DIR / "filters" / "galley-divs.lua").is_file()
    assert manifest.verify(paper) == []


def test_new_paper_front_matter_and_project(tmp_path: Path) -> None:
    paper = scaffold.new_paper("liquidity-2026", tmp_path, git=False, today=date(2026, 10, 5))
    text = (paper / "paper.qmd").read_text(encoding="utf-8")
    matter = yaml.safe_load(text.split("---\n")[1])
    assert matter["title"] == "Liquidity 2026"
    assert matter["date"] == "2026-10-05"
    assert matter["document-id"] == "WP-0000-000"  # from the template's scaffold-metadata
    project = yaml.safe_load((paper / "_quarto.yml").read_text(encoding="utf-8"))
    assert project["format"] == "galley-pdf"
    assert project["execute"]["freeze"] == "auto"
    assert project["project"]["render"] == ["paper.qmd"]
    assert "/whitepaper.cls\n" in (paper / ".gitignore").read_text(encoding="utf-8")


def test_new_paper_rejects_bad_slug_and_existing_dir(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError, match="lowercase"):
        scaffold.new_paper("Bad Slug", tmp_path, git=False)
    scaffold.new_paper("paper-a", tmp_path, git=False)
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold.new_paper("paper-a", tmp_path, git=False)


def test_new_paper_initialises_git(tmp_path: Path) -> None:
    paper = scaffold.new_paper("paper-b", tmp_path)
    assert (paper / ".git").is_dir()
