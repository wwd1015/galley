# Galley — Build Spec for Claude Code

Source: https://claude.ai/artifact/WyndHeTP7nhNhN4JfgWvUY (2026-10-05, Wendi Wang)

## Overview

Galley is a whitepaper build system. Authors write Quarto Markdown, code chunks draw every chart from versioned data, and the output is a PDF typeset through the team's existing LaTeX template with no visual deviation. The name comes from printing: a galley proof is the typeset draft that reviewers mark up before publication, which is what this tool produces and manages.

Project id: `galley`. Python package `galley`, CLI `galley`, Quarto format `galley-pdf`, review app `galley review`.

**Goals**

- Markdown authoring, TeX output: authors never touch LaTeX for normal content; the PDF matches the team template exactly.
- Reproducible exhibits: every chart and table is regenerated from pinned data and code on each build.
- Easy migration: existing whitepapers in Google Docs, Word or PDF convert to Galley format with an automatic fidelity report.
- Review in place: teammates comment on the Markdown source in a GitHub-backed review window and see a live typeset preview with those comments shown.

**Non-goals for v1**

- Real-time character-level co-editing (Google Docs-style CRDT). Comments sync in near real time; text edits flow through Git commits.
- Any cloud service beyond GitHub (github.com or GitHub Enterprise). Everything else runs locally.
- Recovering the data behind charts that exist only as images in PDF sources. Those are flagged for rebuilding.

## Architecture and repo layout

Galley has three paths around one Git repo per paper: conversion brings legacy papers in, build produces the PDF, and review runs on GitHub pull requests.

_(Diagram in the source doc: "Galley architecture · conversion, build and review around one paper repo".)_

galley-verify compares the original document against the built PDF, not against the Markdown, so it checks what readers will actually see.

The tool and the papers live in separate repos. Galley installs with `uv tool install`, and `galley new <slug>` scaffolds a paper repo that vendors the format extension.

```text
galley/                          # tool repo
  galley/                        # Python package + CLI
    cli.py build.py gh.py style.py tables.py
    convert/ verify/ review/     # review/ = Dash app
  _extensions/galley/galley-pdf/
    _extension.yml galley.tex filters/*.lua
  template/                      # team TeX template, read-only
  .claude/skills/galley-convert/SKILL.md
  .claude/skills/galley-verify/SKILL.md
  config/style-map.yaml verify.yaml
  tests/golden/ tests/fixtures/
  .github/workflows/build.yml

<paper-repo>/                    # created by galley new
  _quarto.yml  paper.qmd  sections/  references.bib
  data/ (manifest.yaml)  py/exhibits/  _freeze/
  source/  conversion-report.md  verify-report.md
```

## 1. Format extension: galley-pdf

The team's TeX template becomes a Quarto format extension, so every document only declares `format: galley-pdf` and inherits the exact layout.

1. Copy the team's `.cls`, `.sty`, logos and fonts into `_extensions/galley/galley-pdf/` unchanged. These files are the source of truth and are never edited by Galley.
2. Convert the team's `.tex` into a full Pandoc template (`galley.tex`). Replace hard-coded content with Pandoc variables: `$title$`, `$subtitle$`, `$author$`, `$date$`, `$abstract$`, `$body$`, plus any template-specific fields (document id, classification, version) exposed as YAML metadata.
3. Use `template:` (full template), not `template-partials:`. Quarto's default preamble injects hyperref, geometry and font packages that can conflict with corporate class files.
4. Add only the minimum Pandoc glue the template lacks: `\tightlist`, `longtable`/`booktabs` support, `\pandocbounded` for images, CSL or biblatex hooks. Each addition goes in a clearly marked `% --- galley glue ---` block.
5. Set `pdf-engine` to whatever the template requires, `cite-method: biblatex` if the template uses biblatex, and `keep-tex: true` so the intermediate `.tex` is always inspectable.

**Lua filters** (in the extension, applied automatically):

- `galley-divs.lua`: maps fenced divs to template environments via a YAML table, e.g. `::: {.keyfinding}` → `\begin{keyfinding}`. Adding a new environment is a config change, not code.
- `galley-tables.lua`: restyles Pandoc tables to the template's table conventions (booktabs rules, font size, caption position).
- `galley-review.lua`: active only under the `review` profile; renders review comments as margin notes (see component 5).

**Template fidelity test:** a `tests/golden/` document exercises every template feature (title page, headings to depth 4, figures, tables, footnotes, citations, every custom environment). CI renders it and compares page images against a reference PDF produced by the original `.tex`, failing on any diff above a set threshold.

## 2. Data-driven exhibits and reproducibility

Every chart and table is produced by code from pinned data, and a clean checkout reproduces the same PDF.

**Charts**

- Python is the default engine (Jupyter kernel); R support is optional behind a config flag. _(Confirmed in doc comments: Python default, R optional.)_
- Chunks use Quarto labels and captions: `#| label: fig-loss-curve`, `#| fig-cap: ...`. Cross-references (`@fig-loss-curve`) resolve to LaTeX `\ref`.
- `fig-format: pdf` (vector) by default.
- A `galley.style` module ships a matplotlib style (`galley.mplstyle`) with the team palette, sizes and line weights, and an option to use the pgf backend so chart text uses the template's exact font.
- Chart functions live in `py/exhibits/` as pure functions `(df) -> Figure`. Chunks only load data and call them, so the same exhibit can be unit-tested and reused across papers.

**Tables**

- `galley.tables.to_latex(df, ...)` emits booktabs LaTeX matching the template; chunks print it with `#| output: asis`.
- Number formatting (thousands separators, bp, %, negative style) is centralized in one module.

**Reproducibility**

- `uv` lockfile for Python; a pinned TeX Live version recorded in `galley doctor` output and in an optional Dockerfile.
- `data/` holds versioned inputs. A `data/manifest.yaml` lists each file with its SHA-256, source and as-of date; the build fails if a hash does not match.
- `execute: freeze: auto` so documents only re-execute when their code changes; `_freeze/` is committed.
- `galley build` is the single entry point: verify manifest → render → run checks → write `build-report.json` (git SHA, data hashes, tool versions).
- A GitHub Actions workflow runs `galley build` on every push and attaches the PDF to the run.

## 3. Skill: galley-convert

A Claude Code skill at `.claude/skills/galley-convert/SKILL.md`, backed by the deterministic CLI `galley convert <input> --out papers/<slug>/`. The CLI does the mechanical extraction; the skill uses Claude for the judgment calls (style mapping, cleanup, flagging) and always finishes by invoking galley-verify.

**Inputs and extraction path**

| Source | Path | Notes |
| --- | --- | --- |
| Google Doc | Export to .docx (Drive API or user download), then the Word path | Keep the export alongside as `source/original.docx`; record the Doc URL in metadata |
| Word (.docx) | Pandoc docx → Markdown with `--extract-media`, plus python-docx for style names | Embedded Word/Excel charts carry their data in an internal workbook: extract it to CSV and generate a real chart chunk |
| PDF | Layout-aware extraction (docling or marker; pdfplumber fallback for tables) | Charts are raster/vector images with no data: insert as static figures marked `needs-data` |

**Conversion rules**

- Map source styles to Galley constructs via `config/style-map.yaml` (e.g. Word style "Key Finding" → `::: {.keyfinding}`). Unknown styles are listed in the report, never silently dropped.
- Headings, lists, footnotes, captions, cross-references and citations become native Quarto syntax. Citations are matched to `references.bib` where possible; unmatched ones become `[@TODO-n]` with the original text in a comment.
- Tables become CSVs in `data/tables/` rendered via `galley.tables`, so they are data-driven from day one.
- Every figure gets a label and caption; image-only figures carry `#| galley-status: needs-data`.
- Output structure: `paper.qmd` (or `sections/*.qmd` for long papers), `data/`, `figures/source/`, `source/` (the original file, read-only), and `conversion-report.md`.

**Conversion report** lists: unmapped styles, figures needing data, unresolved citations, any content the converter was unsure about with source page/paragraph references, and the galley-verify result.

## 4. Skill: galley-verify

A Claude Code skill at `.claude/skills/galley-verify/SKILL.md`, backed by `galley verify <source> <paper-dir>`. It runs automatically at the end of every conversion, as a pre-commit hook on any converted paper, and in CI. It compares the original file with the rendered Galley PDF and fails loudly on content loss.

**Checks** (deterministic; thresholds in `config/verify.yaml`)

| Check | Method | Default pass rule |
| --- | --- | --- |
| Text coverage | Normalize both texts (whitespace, hyphenation, ligatures, quotes), align by section, diff | ≥ 99.5% of source tokens present |
| Numbers | Extract every numeric token (%, bp, $, dates, decimals) from both; compare as multisets | 100% match; every mismatch listed with context |
| Structure | Compare heading trees (level + text) | Identical order and count |
| Exhibits | Count figures and tables; match captions | Counts equal; captions ≥ 98% similar |
| Footnotes and citations | Count and match text | Counts equal; unresolved citations listed |
| Visual (advisory) | Render both to page images; compare per-figure crops | Report only, since layout intentionally changes |

The numbers check is the most important: a dropped or altered figure in a risk whitepaper is a material error, so any numeric mismatch is a hard failure.

**Claude's role in the skill:** after the deterministic checks, Claude reviews each flagged diff and classifies it as acceptable (formatting only), fixable (it proposes the edit) or needs human review. It never marks a numeric mismatch as acceptable on its own.

**Output:** `verify-report.md` (human-readable, with side-by-side snippets and source page references) and `verify-report.json` (machine-readable, consumed by CI and by the review app's status badge). Exit code non-zero on any hard failure.

## 5. Galley Review: local Dash app

`galley review` launches a local Dash app (localhost only) that recreates GitHub's pull-request review window for `.qmd` files and shows a live typeset preview, with every comment visible in both panes. GitHub is the only shared state: all comments are real PR review comments, so the team sees the same threads in the app and on GitHub.

**GitHub integration (via `gh` CLI only)**

- All calls go through a thin `galley.gh` wrapper around `gh api` / `gh pr` with JSON output. No tokens stored by Galley; auth comes from `gh auth`. Supports GitHub Enterprise hosts via `--hostname`.
- A review session = one pull request. The app lists open PRs that touch `.qmd` files, or opens a new one from the current branch.
- Read: PR review comments and threads (`pulls/{n}/comments`, GraphQL `reviewThreads` for resolved state).
- Write: new line comment, reply to thread, resolve/unresolve thread, edit/delete own comment, submit review (comment / approve / request changes).
- **Known constraint to handle:** GitHub only allows line-level comments on lines inside the PR diff. For lines outside the diff, post a file-level comment (`subject_type: file`) with a hidden anchor `<!-- galley:anchor line=42 quote="first 60 chars" -->`. The app parses anchors and displays these as line comments. Prototype this in phase 1; it is the main technical risk.
- Re-anchoring: when the file changes, comments are re-located by quoted text first, line number second; ones that cannot be placed are shown as "outdated", like GitHub.

**Near-real-time sync**

- Poll comments every 10 seconds with ETag conditional requests (304 responses do not count against the rate limit). Back off to 60 seconds when the window is idle.
- Poll the PR head SHA the same way; when a teammate pushes, show a banner with "Pull and refresh". Never auto-overwrite local unsaved edits.
- Edits are saved locally, then "Commit & push" commits to the PR branch with a generated message. Conflicts fall back to a standard Git merge flow with a clear message.

**Layout**

- Top bar: PR picker, branch, sync status (last poll, new comment count), galley-verify badge, render status.
- Left pane: source editor (Ace or CodeMirror component) with line numbers, a "+" on gutter hover to start a comment, and threads expanded inline under their lines exactly like GitHub's Files changed view. Avatars, timestamps, Markdown in comment bodies, reply box, resolve button.
- Right pane: live preview. Default is fast HTML (seconds); a "PDF proof" toggle renders the true `galley-pdf` output and shows it with PDF.js.
- Scroll sync between panes; clicking a comment marker in the preview jumps to its source line and vice versa.

**Comments in the preview**

- Before each preview render, Galley writes a temporary copy of the `.qmd` with an anchor span `[]{.galley-comment ref=c123}` at each commented line. The source file is never modified.
- Under the `review` profile, `galley-review.lua` turns anchors into numbered margin notes (HTML: side notes; PDF: `todonotes`-style margin boxes), color-coded open vs resolved, with author initials.
- Render runs in a background worker, debounced 1.5 seconds after the last keystroke, cancelling stale renders. If a render fails, the previous preview stays and the error shows in the status bar.

## 6. Build phases and kickoff prompt

Build in five phases. Each phase ends with its acceptance test passing in CI before the next starts.

1. **Format extension.** `galley-pdf` extension, Pandoc template, div and table filters, golden test document. Done when the golden document's page images match the reference PDF.
2. **Exhibits and build.** `galley.style`, `galley.tables`, data manifest, `galley build`, CI workflow, `galley doctor`. Done when a clean checkout reproduces a byte-identical `build-report.json` (except timestamps) and the sample paper's charts use the template font.
3. **Verify.** `galley verify` and its skill. Done when it catches deliberately injected errors in a test fixture: one dropped paragraph, one changed number, one missing figure, one renamed heading.
4. **Convert.** `galley convert` for docx (and Google Doc via docx), then PDF, plus the skill. Done when three real legacy whitepapers convert with verify passing or every failure explained in the report.
5. **Review app.** Spike the off-diff comment anchoring first, then sync, editor, preview and margin notes. Done when two users on separate machines comment and reply on the same PR and each sees the other's comment in both panes within 15 seconds.

**Kickoff prompt** (paste into Claude Code with this spec saved as `SPEC.md` and the team template in `template/`):

```text
Read SPEC.md fully. You are building Galley, described there.

Rules:
- Work phase by phase in the order in section 6. Do not start a phase until the previous phase's acceptance test passes.
- Before writing code for a phase, write a short plan in PLAN.md (files, functions, tests) and stop for my approval.
- The files in template/ are the source of truth for layout. Never edit them; copy them into the extension.
- Python 3.12 with uv. Ruff + mypy strict. pytest for every module; fixtures live in tests/fixtures/.
- All GitHub access goes through the gh CLI via galley/gh.py. Never store tokens.
- Keep deterministic logic in the galley CLI; skills in .claude/skills/ call the CLI and only use judgment for mapping, cleanup and triage.
- Commit at each green test with conventional commit messages.
- If something in SPEC.md is ambiguous or conflicts with what you find in the template, ask me instead of guessing.

Start with Phase 1: inspect template/, list every variable, environment and package it uses, and propose the Pandoc template conversion in PLAN.md.
```
