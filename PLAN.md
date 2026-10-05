# PLAN

All five phases in SPEC.md section 6 are built. 178 tests pass locally with
ruff and mypy strict clean. This file records what was built, where it departs
from the spec, and what has not been proven.

## Status by phase

| Phase | Acceptance test in the spec | Result |
| --- | --- | --- |
| 1. Format extension | Golden document's page images match the reference PDF | Met locally: all 3 pages pixel-identical (`tests/test_fidelity.py`). |
| 2. Exhibits and build | Clean checkout reproduces `build-report.json` byte-for-byte except timestamps; charts use the template font | Met locally (`tests/test_build.py`). |
| 3. Verify | Catches a dropped paragraph, a changed number, a missing figure, a renamed heading | Met locally (`tests/test_verify_integration.py`). |
| 4. Convert | Three real legacy whitepapers convert with verify passing or every failure explained | Met only on synthetic papers: two generated `.docx` files and one typeset PDF (`tests/test_convert.py`). No real legacy paper has been converted. |
| 5. Review app | Two users on separate machines see each other's comment in both panes within 15 seconds | Simulated: two clones and a fake `gh` (`tests/test_review_session.py`), plus a click-through in a headless browser. Never run against real GitHub. |

## The app (added after the five phases)

`galley app <workspace>` puts every step in one window: Papers, Convert,
Build, Verify and Review tabs (`galley/workbench.py`, `assets/galley-app.js`).
Long steps run as background jobs and report each stage. It was clicked
through in a headless browser: doctor, a Word conversion, a build, the verify
results, and a review with two actions sent in the same instant. The embedded
PDF on the Build tab did not draw in the headless browser (it has no PDF
viewer), so that one view is unconfirmed; the PDF itself is served correctly.

## Not proven

- **CI has never run.** The repo has no remote. `.github/workflows/build.yml`
  is written but untested, so "passing in CI" is unmet for every phase.
- **`galley/gh.py` has only talked to `tests/fake_gh.py`.** The fake encodes
  GitHub's documented behaviour (422 for a line outside the diff, ETag/304,
  `subject_type: file`, GraphQL thread resolution). Real `gh` may differ in
  details such as how `gh api --include` reports a 304. Try `galley review` on
  a real pull request before relying on it.
- **Real documents.** Conversion and verify heuristics (headings, captions,
  footnotes, tables in PDFs) were tuned on the stand-in template's output.
- **The real team template.** Everything runs on the stand-in in `template/`.

## Design: template-agnostic

The spec describes hand-converting one team template. Galley treats the
template as data so any template can be adopted without code changes:
`template/` holds the team's files, `config/template.yaml` describes them
(engine, citation method, text-to-variable replacements, environments, table
and chart conventions, scaffold front matter), and `galley template adopt`
generates the extension. `galley.yml` inside the extension carries the
settings into each paper repo.

## Deviations from SPEC.md

| Spec | Built | Why |
| --- | --- | --- |
| Extension at `_extensions/galley/galley-pdf/` | `_extensions/galley/galley/` | Quarto names a format `<extension>-<base>`; the spec's path would give `galley-pdf-pdf`. |
| Fidelity diff "above a set threshold" | Threshold is zero pixels | A looser one hid Quarto reformatting the title-page date. |
| biblatex if the template uses it | Stand-in uses natbib/bibtex | `biber` fails on this machine until the Xcode licence is accepted. biblatex is supported by config. |
| PDF extraction with docling or marker, pdfplumber fallback | PyMuPDF only | Avoids large model downloads; figure, table, footnote and heading extraction are implemented on PyMuPDF. |
| Image-only figures carry `#| galley-status: needs-data` | `galley-status="needs-data"` attribute on the image | `#|` options only exist inside code chunks. |
| Google Doc export through the Drive API | Export URL for link-shared docs; otherwise download as `.docx` | No credentials are stored. The skill can use a Drive connector. |
| PDF proof shown with PDF.js | The browser's built-in PDF viewer | Margin notes show in the PDF, but clicking a note and scroll sync work only in the fast HTML preview. |
| Scroll sync between panes | Proportional (same fraction of each pane) | Exact line mapping needs source positions in the output. |
| Verify compares the PDF only | Heading tree and footnote count come from the intermediate `.tex`; all text and numbers come from the PDF | Run-in headings and footnote markers are not reliably recoverable from PDF text. |
| Off-diff comment spike in phase 1 (section 5) | Done in phase 5 (section 6) | The two sections disagree. |

## Not built

- **R support behind a config flag.** Python only.
- **Optional Dockerfile** pinning TeX Live. `galley doctor` reports the TeX Live year.
- **Citation count check** in verify. Unresolved citations are listed and the
  source's reference list is compared loosely with the generated bibliography.
- **Per-figure crops** in the advisory visual check. It writes whole pages
  that contain figures, from both documents.

## Things verify and the fidelity test caught during the build

- Quarto reformatted `date: "October 2026"` to an ISO date (fixed with `format-options`).
- Quarto emits a longtable caption and its row end as separate paragraphs, shifting the caption by half a space (fixed in `galley-latex.lua`).
- Quarto ignores `tbl-pos` for tables printed by chunks as raw LaTeX (fixed in `galley-latex.lua`).
- A chunk ending in `paper.setup()` printed a file path into the PDF (now returns nothing).

## Local environment notes

- `uv run galley` fails here because macOS hides the virtualenv's `.pth` file.
  Use `.venv/bin/python -m galley ...` from the repo root.
- TinyTeX is at `~/Library/TinyTeX`, not on `PATH`; Galley finds it itself.
