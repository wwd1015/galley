# Galley

Whitepaper build system. Authors write Quarto Markdown, code chunks draw every
chart from versioned data, and the output is a PDF typeset through the team's
existing LaTeX template. See [SPEC.md](SPEC.md) for the full design and
[PLAN.md](PLAN.md) for build status.

## Setup

```sh
uv sync
quarto install tinytex                      # or any TeX Live install
tlmgr install $(uv run galley template packages)
uv run galley doctor                        # reports versions and anything missing
uv run pytest
```

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Quarto 1.4 or later, and
the [`gh` CLI](https://cli.github.com) for `galley review`.

## The app

```sh
uv run galley app ~/papers        # any folder that holds, or will hold, your paper repos
```

One local window (127.0.0.1 only) with a tab for each step:

| Tab | What you do there |
| --- | --- |
| Papers | See every paper in the workspace with its build and verify status, create a new paper, run the toolchain check. |
| 1 Convert | Drop a `.docx` or `.pdf` (or give a path or a link-shared Google Docs URL), name the paper, convert. The conversion report appears below. |
| 2 Build | Build the PDF. Shows the data manifest, the build checks, tool versions and the PDF. |
| 3 Verify | Compare the original with the rendered PDF. Shows each check and finding, the side-by-side images, and lets a person accept a finding. |
| 4 Review | The pull request review with inline comments and live preview. Needs the paper pushed to GitHub with an open PR. |

A strip under the tabs shows the running step and each stage it has passed.
Every tab does what the matching command below does.

## Commands

| Command | Purpose |
| --- | --- |
| `galley new <slug>` | Scaffold a paper repo with an example chart and table, the vendored extension and a CI workflow. |
| `galley build [dir]` | Verify the data manifest, render the PDF, run checks, write `build-report.json`. |
| `galley data add <file>` / `galley data verify` | Pin a data file by SHA-256 in `data/manifest.yaml`; check every file against it. |
| `galley convert <input> --out <dir>` | Convert a `.docx`, a link-shared Google Doc URL or a `.pdf` into a paper repo, then verify it. |
| `galley verify <source> [dir]` | Compare the original document with the rendered PDF; non-zero exit on content loss. |
| `galley hooks [dir]` | Install a pre-commit hook that runs `galley verify` on a converted paper. |
| `galley app [workspace]` | The app: convert, build, verify and review in one window. |
| `galley review [dir]` | Open the app directly on a paper's pull request review. |
| `galley doctor [dir]` | Tool versions, including the TeX Live year, and missing TeX packages. |
| `galley template ...` | Adopt and test the team template (below). |

## Writing a paper

```sh
galley new liquidity-2026 && cd liquidity-2026
galley build .
```

- `paper.qmd` declares nothing about layout; `_quarto.yml` sets `format: galley-pdf`.
- Chart functions live in `py/exhibits/` as pure `(df) -> Figure` functions.
  Chunks load data and call them. Build the `Figure` directly rather than
  through `pyplot`, so a chunk shows it once.
- `paper.setup()` in the first chunk makes `py/` importable and applies the
  chart style. With `style.pgf: true` in the template config, chart text is
  typeset by the template's TeX engine in the template's fonts.
- Tables: `print(tables.to_latex(df, formats={"rate": "percent:1"}))` in a
  chunk with `#| output: asis`. Number formats (`number`, `percent`, `bp`,
  `currency`) live in `galley.formatting`.
- Every file under `data/` must be in `data/manifest.yaml`; the build fails on
  a hash mismatch or an unlisted file.
- `_freeze/` is committed, so a clean checkout only re-runs code that changed.

## Converting a legacy paper

```sh
galley convert old-paper.docx --out papers/old-paper --bib references.bib
```

Styles map to template environments through `config/style-map.yaml`; unknown
styles are reported, never dropped. Tables become CSVs, Word charts are rebuilt
from their embedded data, and images with no data are marked
`galley-status="needs-data"`. `conversion-report.md` lists everything a person
must look at and ends with the `galley verify` result. The
`galley-convert` and `galley-verify` skills in `.claude/skills/` drive the
judgment calls on top of these commands.

To accept a difference `galley verify` flags, add its id to
`verify-accept.yaml` in the paper with a `reason`. A numeric difference also
needs `approved-by` naming a person; the CLI rejects anything else.

## Reviewing

```sh
cd <paper-repo> && galley review . --pr 12      # --hostname for GitHub Enterprise
```

Runs on `127.0.0.1` only and uses your `gh auth` login; no token is stored.
Hover a line number and press **+** to comment. Comments are real pull request
review comments, so they also appear on GitHub. A line outside the PR's diff
gets a file-level comment with a hidden anchor, which the app shows on its
line. Edits are saved to your working tree; **Commit & push** sends them to
the PR branch. The right pane re-renders 1.5 seconds after you stop typing.

## How a template is adopted

Galley's code knows nothing about any particular template. Three things
describe one:

| Where | What |
| --- | --- |
| `template/` | The team's `.cls`, `.sty`, sample `.tex`, logos and fonts. Never edited. |
| `config/template.yaml` | Which file is the sample `.tex`, which files to vendor, the TeX engine and citation method, how the sample's hard-coded content maps to Pandoc variables, and which fenced-div classes map to which environments. |
| `tests/golden/` | `golden.qmd` and a hand-written `reference.tex` with the same content, exercising every template feature. |

`galley template adopt` reads the first two and generates the Quarto extension
in `_extensions/galley/galley/`: byte-identical copies of the team's files, a
full Pandoc template (`galley.tex`) and `_extension.yml`. Documents then
declare `format: galley-pdf`.

The only text Galley adds to the team's `.tex` sits between
`% --- galley glue ---` markers (see `galley/data/glue.tex`).

### Commands

| Command | Purpose |
| --- | --- |
| `galley template adopt` | Regenerate the extension from `template/` and `config/template.yaml`. |
| `galley template check` | Fail if the committed extension is out of date. |
| `galley template reference` | Rebuild `tests/golden/reference.pdf` from `reference.tex` and the team's unmodified files. |
| `galley template verify` | Render `golden.qmd` through the extension and compare page images with `reference.pdf`. Thresholds are in `config/fidelity.yaml`; failing pages are written to `.galley-build/diff/`. |
| `galley template packages` | Print the TeX Live packages the template needs. |

### Switching to a different template

The files in `template/` today are a **stand-in** written for development.
To adopt the real one:

1. Replace the contents of `template/`.
2. Update `config/template.yaml`: `main`, `resources`, `engine`,
   `cite-method`, `tex-packages`, `replacements`, `environments`, `tables`.
3. Run `galley template adopt`.
4. Rewrite `tests/golden/golden.qmd` and `reference.tex` to cover the new
   template's features, then run `galley template reference`.
5. Run `galley template verify` and fix any page that differs.

No Python or Lua changes are needed unless the new template has a convention
the config cannot yet express.

### Lua filters

| Filter | Purpose |
| --- | --- |
| `galley-divs.lua` | `::: {.keyfinding}` becomes `\begin{keyfinding}`, driven by `environments` in the config. |
| `galley-tables.lua` | Applies the table conventions in `tables`. |
| `galley-latex.lua` | Removes Quarto artefacts that shift output away from the template. |
| `galley-review.lua` | Review margin notes (phase 5; inert for now). |
