"""``galley new``: scaffold a paper repo that vendors the galley-pdf extension."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from galley import manifest
from galley.config import extension_source, read_paper_config
from galley.template import EXTENSION_DIR, PAPER_CONFIG

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

QUARTO_YML = """\
project:
  title: "{slug}"
  # `galley build` renders the first document listed here.
  render:
    - paper.qmd

format: galley-pdf

execute:
  freeze: auto
  echo: false
  warning: false

jupyter: python3
"""

REVIEW_PROFILE = """\
# Applied by `galley review` (quarto --profile review): comments become margin notes.
galley-review: true
"""

GITIGNORE = """\
.quarto/
/*.pdf
/*.tex
/*_files/
*.galley-preview.*
*.galley-preview_files/
.galley-build/
build-report.json
__pycache__/
"""

EXAMPLE_CSV = """\
month,buffer,outflows
2026-01,118,100
2026-02,121,100
2026-03,116,100
2026-04,124,100
2026-05,129,100
2026-06,127,100
"""

EXHIBIT = '''\
"""Example exhibit. Exhibits are pure functions ``(df) -> Figure``."""

from __future__ import annotations

import pandas as pd
from matplotlib.figure import Figure


def buffer_chart(df: pd.DataFrame) -> Figure:
    """Liquidity buffer against net outflows, by month."""
    # Build the Figure directly (not through pyplot) so a chunk shows it once.
    figure = Figure()
    axes = figure.subplots()
    axes.plot(df["month"], df["buffer"], label="Liquidity buffer")
    axes.plot(df["month"], df["outflows"], label="Net outflows", linestyle="--")
    axes.set_ylabel("Index (net outflows = 100)")
    axes.legend()
    return figure
'''

PAPER = """\
---
{front_matter}---

```{{python}}
import pandas as pd
from galley import paper, tables

paper.setup()
from exhibits.example import buffer_chart

data = pd.read_csv("data/example.csv")
```

# Introduction {{#sec-intro}}

Replace this text. @fig-buffer is drawn from `data/example.csv` on every
build, and @tbl-buffer prints the same data.

```{{python}}
#| label: fig-buffer
#| fig-cap: "Liquidity buffer against net outflows"
buffer_chart(data)
```

```{{python}}
#| label: tbl-buffer
#| tbl-cap: "Liquidity buffer by month"
#| output: asis
print(tables.to_latex(data, formats={{"buffer": "number", "outflows": "number"}}))
```
"""

WORKFLOW = """\
name: build

on:
  push:
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: quarto-dev/quarto-actions/setup@v2
        with:
          tinytex: true
      # Set the repository variable GALLEY_SOURCE to where Galley is installed
      # from, e.g. git+https://github.com/<org>/galley.
      - name: Install Galley
        run: uv tool install "${{ vars.GALLEY_SOURCE || 'galley' }}"
      - name: Install the template's TeX packages
        run: |
          echo "$HOME/.TinyTeX/bin/x86_64-linux" >> "$GITHUB_PATH"
          export PATH="$HOME/.TinyTeX/bin/x86_64-linux:$PATH"
          packages="$(galley template packages --paper .)"
          if [ -n "$packages" ]; then tlmgr install $packages; fi
      - name: Build
        run: galley build .
      - name: Verify against the original document
        if: hashFiles('source/original.*') != ''
        run: galley verify "$(ls source/original.* | head -n 1)" . --no-build
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: paper
          path: |
            *.pdf
            build-report.json
            verify-report.md
            verify-report.json
"""


class ScaffoldError(Exception):
    """The paper repo could not be created."""


def title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").capitalize()


def vendor_extension(paper_dir: Path) -> Path:
    """Copy the galley-pdf extension into ``paper_dir`` (replacing an older copy)."""
    target = paper_dir / EXTENSION_DIR
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(extension_source(), target)
    return target


def front_matter(title: str, extra: dict[str, Any], **fields: Any) -> str:
    """YAML front matter for a paper, with the template's own fields included."""
    document: dict[str, Any] = {"title": title, **fields, **extra}
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def format_resources(paper_dir: Path) -> list[str]:
    """Template files Quarto copies beside the document on every render."""
    raw = yaml.safe_load((paper_dir / EXTENSION_DIR / "_extension.yml").read_text("utf-8"))
    return [str(r) for r in raw["contributes"]["formats"]["pdf"].get("format-resources", [])]


def write_project_files(paper_dir: Path, slug: str) -> None:
    """Files every paper repo has, whether new or converted."""
    (paper_dir / "_quarto.yml").write_text(QUARTO_YML.format(slug=slug), encoding="utf-8")
    (paper_dir / "_quarto-review.yml").write_text(REVIEW_PROFILE, encoding="utf-8")
    copied = "".join(f"/{name}\n" for name in format_resources(paper_dir))
    (paper_dir / ".gitignore").write_text(
        GITIGNORE + "# Template files copied in from the extension at render time.\n" + copied,
        encoding="utf-8",
    )
    workflow = paper_dir / ".github" / "workflows" / "build.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(WORKFLOW, encoding="utf-8")
    for directory in ("data", "py/exhibits", "sections", "source"):
        (paper_dir / directory).mkdir(parents=True, exist_ok=True)
    init = paper_dir / "py" / "exhibits" / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    for keep in ("sections", "source"):
        (paper_dir / keep / ".gitkeep").touch()


def git_init(paper_dir: Path) -> bool:
    if shutil.which("git") is None:
        return False
    result = subprocess.run(
        ["git", "init", "-q"], cwd=paper_dir, capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def new_paper(slug: str, parent: Path, *, git: bool = True, today: date | None = None) -> Path:
    """Create ``parent/slug`` with a buildable example paper."""
    if not SLUG.match(slug):
        raise ScaffoldError("slug must be lowercase letters, digits and hyphens")
    paper_dir = parent / slug
    if paper_dir.exists() and any(paper_dir.iterdir()):
        raise ScaffoldError(f"{paper_dir} already exists and is not empty")
    paper_dir.mkdir(parents=True, exist_ok=True)

    extension = vendor_extension(paper_dir)
    config = read_paper_config(extension / PAPER_CONFIG)
    write_project_files(paper_dir, slug)

    matter = front_matter(
        title_from_slug(slug),
        config.scaffold_metadata,
        author=["Author Name"],
        date=(today or date.today()).isoformat(),
        abstract="One paragraph summarising the paper.",
    )
    (paper_dir / "paper.qmd").write_text(PAPER.format(front_matter=matter), encoding="utf-8")
    (paper_dir / "references.bib").write_text(
        "% Add BibTeX entries here, then set `bibliography: references.bib` in paper.qmd.\n",
        encoding="utf-8",
    )
    (paper_dir / "data" / "example.csv").write_text(EXAMPLE_CSV, encoding="utf-8")
    (paper_dir / "py" / "exhibits" / "example.py").write_text(EXHIBIT, encoding="utf-8")
    manifest.save(paper_dir, [])
    manifest.add(
        paper_dir,
        Path("data/example.csv"),
        source="Example data written by `galley new`",
        as_of=(today or date.today()).isoformat(),
    )
    if git:
        git_init(paper_dir)
    return paper_dir
