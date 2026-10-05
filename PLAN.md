# PLAN

## Phase 1 — Format extension (`galley-pdf`): built, passing locally

Acceptance test: `tests/test_fidelity.py::test_golden_document_matches_reference`
(also `galley template verify`). All three pages of the golden document are
pixel-identical to the reference PDF at 100 dpi. It has not run in GitHub
Actions yet because the repo has no remote.

### Design change: template-agnostic adoption

The spec describes hand-converting one team template. Galley instead treats
the template as data, so any template can be adopted without code changes:

| Piece | Role |
| --- | --- |
| `template/` | Team files, never edited. Currently a stand-in written for development. |
| `config/template.yaml` | Engine, citation method, files to vendor, literal-text → Pandoc-variable replacements, div → environment map, table conventions, extra Quarto format options. |
| `galley/template.py` | `adopt` generates the extension; `check` detects drift. |
| `galley/data/glue.tex` | The `% --- galley glue ---` block, the only text added to the team's `.tex`. |
| `galley/fidelity.py` | Rasterise and compare PDFs; build the reference; render the golden document. |
| `_extensions/galley/galley/filters/*.lua` | `galley-divs`, `galley-tables`, `galley-latex` (post-render fix-ups), `galley-review` (stub until phase 5). |
| `tests/golden/` | `golden.qmd`, `reference.tex`, committed `reference.pdf`. |

### Deviations from SPEC.md

- **Extension path** is `_extensions/galley/galley/`, not
  `_extensions/galley/galley-pdf/`. Quarto names a format
  `<extension>-<base format>`, so the spec's path would yield `galley-pdf-pdf`.
- **Environment map lives in `config/template.yaml`** and reaches the Lua
  filter through format metadata, rather than a separate YAML file read by the
  filter.
- **Fidelity threshold is zero** differing pixels per page. A loose threshold
  hid a real error during development (Quarto reformatting the date), and a
  single changed digit is only about 0.003% of a page.

### What the fidelity test caught

- Quarto reformatted `date: "October 2026"` to an ISO date. Fixed with
  `format-options: date-format` in the config.
- Quarto emits a longtable caption and its row end as separate paragraphs,
  shifting the caption by half a space. Fixed in `galley-latex.lua`.

### Open items

- **Real template.** Swap it in following the README; the golden document and
  reference must be rewritten for its features.
- **biber on this machine.** `biber` fails here until the Xcode licence is
  accepted (`sudo xcodebuild -license`). The stand-in uses natbib/bibtex to
  avoid it. A real template that uses biblatex with biber will need that fixed
  locally; CI on Linux is unaffected.
- **CI.** `.github/workflows/build.yml` is written but unrun.
- **Off-diff comment anchoring spike.** Section 5 says phase 1, section 6 says
  phase 5. Left for phase 5.

## Phase 2 — Exhibits and build: proposed, awaiting approval

| File | Content |
| --- | --- |
| `galley/formatting.py` | `fmt_number`, `fmt_percent`, `fmt_bp`, `fmt_currency`; one `NumberStyle` (thousands separator, decimals, negative style: minus or parentheses). |
| `galley/tables.py` | `to_latex(df, *, caption, label, formats, align) -> str` emitting booktabs LaTeX in the template's table convention from `config/template.yaml`. |
| `galley/style.py`, `galley/data/galley.mplstyle` | `use(pgf=False)` applies the style; palette, sizes and fonts come from a new `style:` block in `config/template.yaml` so charts follow whichever template is adopted. `pgf=True` sets the pgf backend with the template's engine and font preamble. |
| `galley/manifest.py` | `load`, `verify(paper_dir) -> list[Mismatch]`, `add(path, source, as_of)`; SHA-256 per file in `data/manifest.yaml`. |
| `galley/build.py` | `build(paper_dir)`: verify manifest → `quarto render` → checks (unresolved refs, missing citations, `needs-data` figures) → `build-report.json` (git SHA, data hashes, tool versions; timestamps isolated in one field). |
| `galley/doctor.py` | Reports Python, uv, Quarto, Pandoc, TeX Live and engine versions and missing `tex-packages`. |
| `galley/scaffold.py` | `galley new <slug>`: paper repo with `_quarto.yml` (`freeze: auto`), `paper.qmd`, `data/manifest.yaml`, `py/exhibits/`, vendored extension, build workflow. The extension ships inside the wheel so `uv tool install` works. |
| `cli.py` | `galley build`, `galley doctor`, `galley new`, `galley data add/verify`. |
| `tests/fixtures/sample-paper/` | A paper with one chart and one table from CSV. |
| `Dockerfile` (optional) | Pinned TeX Live. |

Tests: unit tests per module; manifest tamper test (build fails on hash
mismatch); two clean builds give identical `build-report.json` apart from the
timestamp field; the sample paper's chart PDF embeds the template font
(checked with PyMuPDF's font list).

Acceptance: a clean checkout reproduces a byte-identical `build-report.json`
except timestamps, and the sample paper's charts use the template font.

Dependencies to add: `pandas`, `matplotlib`, `jupyter`/`ipykernel`.
