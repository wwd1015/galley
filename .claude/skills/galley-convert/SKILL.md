---
name: galley-convert
description: Convert an existing whitepaper (Word .docx, Google Doc, or PDF) into a Galley paper repo, then verify nothing was lost. Use when asked to migrate, import or convert a legacy paper into Galley or Quarto.
---

# galley-convert

`galley convert` does the mechanical extraction. You handle the judgment
calls: style mapping, cleanup and flagging. Always finish with galley-verify.

## 1. Get a file

| Source | What to do |
| --- | --- |
| Word `.docx` | Use it as is. |
| Google Doc | Export to `.docx` (Drive connector if available, or ask the user to use File > Download > Microsoft Word). A link-shared doc can be passed as its URL. Always pass `--source-url <doc url>` so the origin is recorded. |
| PDF | Use it as is. Ask whether a `.docx` exists first: Word keeps styles, footnotes and chart data that a PDF has lost. |

## 2. Convert

```sh
galley convert <input> --out papers/<slug>/ [--bib references.bib] [--source-url <url>]
```

`<slug>` is lowercase with hyphens. Pass `--bib` if the team has a BibTeX file;
without it every citation becomes `[@TODO-n]`. The command writes the paper
repo, runs `galley verify`, and writes `conversion-report.md`. Exit code 1
means verify failed, which is normal on a first pass.

What it produces:

- `paper.qmd` (or `sections/*.qmd` for long papers) with native Quarto headings, lists, footnotes, cross-references and citations
- `data/tables/*.csv`: every table, printed through `galley.tables` exactly as written
- `data/charts/*.csv` and `py/exhibits/converted.py`: Word charts rebuilt from their embedded data
- `figures/source/`: images with no data behind them, marked `galley-status="needs-data"`
- `source/original.*`: the original, read-only. Never edit it.

## 3. Work through `conversion-report.md`

Take each section in turn. Never delete content to make a check pass.

**Unmapped styles.** For each style, read its first use and decide:
- It means something the template has an environment for (see `environments`
  in `config/template.yaml`): add `<Style name>: {div: <class>}` under
  `styles` in `config/style-map.yaml`.
- It is presentational only: add it to `ignore`.
- It means something the template has no environment for: leave it reported
  and tell the user; do not invent an environment.

After changing the style map, convert again into a fresh directory rather than
hand-editing every occurrence.

**Unresolved citations.** For each `[@TODO-n]`, look for the work in
`references.bib`. If it is there under another spelling, replace the key. If
not, leave the TODO and list it for the user; do not invent bibliography
entries.

**Figures needing data.** Leave them marked. Rebuilding a chart needs its real
data, which only the authors have. List them for the user.

**Unsure.** Read each item against the source at the location given and fix
the `.qmd` where the converter guessed wrong (a missing caption, a merged
table cell, title-page fields that belong in front matter).

**Cleanup you may do unprompted:** join paragraphs split by a page break in a
PDF, fix list items that were merged, restore a heading's level, move title
page fields (document id, version, classification, authors, date) into front
matter.

## 4. Verify

Run the galley-verify skill on `source/original.*` and the paper directory,
and triage what it reports. Numeric mismatches are never yours to accept.

## 5. Report back

State the verify result, what you changed, and three lists for the user:
figures needing data, citations still unresolved, and anything needing human
review. A conversion is done when verify passes or every remaining failure is
explained in `conversion-report.md`.
