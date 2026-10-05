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
uv run pytest
```

Needs Python 3.12, [uv](https://docs.astral.sh/uv/) and Quarto 1.4 or later.

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
