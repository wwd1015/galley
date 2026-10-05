---
name: galley-verify
description: Check that a Galley paper lost nothing from its original Word or PDF document. Runs `galley verify`, then triages each flagged difference. Use after converting a paper, when verify fails in CI or pre-commit, or when asked whether a converted paper matches its source.
---

# galley-verify

`galley verify` compares the original document with the **rendered PDF**, so it
checks what readers will see. The CLI does every comparison; your job is to
triage what it flags.

## 1. Run the checks

```sh
galley verify <source> <paper-dir>      # source is .docx or .pdf, usually source/original.*
```

It builds the paper, then writes `verify-report.md` (read this) and
`verify-report.json` (ids and details) into the paper directory. Exit code 0
means every hard check passed, 1 means at least one failed, 2 means it could not
run (fix that first: usually a render error shown in the output).

| Check | Fails when |
| --- | --- |
| Text coverage | Under 99.5% of source words are in the PDF, or 8+ consecutive words are missing |
| Numbers | Any number in the source is missing or altered, or the PDF has a number the source lacks |
| Structure | The heading trees differ in order, level or text |
| Exhibits | Figure or table counts differ, or a caption is under 98% similar |
| Footnotes and citations | Footnote counts differ (unresolved citations are listed, not failed) |
| Visual | Never; it writes images to `verify-visual/` for a person to compare |

Thresholds live in `config/verify.yaml` (or a `verify.yaml` in the paper).

## 2. Triage every finding

Read each row of `verify-report.md` and open the source and the `.qmd` at the
location given. Put each finding in exactly one class:

- **Acceptable**: formatting only. The content is there but reads differently
  to the checker (a heading the template renumbers, a caption label, a
  ligature). Add it to `verify-accept.yaml` with a specific `reason`.
- **Fixable**: content really is missing or changed in the `.qmd`. Propose the
  exact edit, apply it if asked to, and re-run `galley verify`.
- **Needs human review**: you cannot tell from the two documents which is
  right, or the fix is a judgment about meaning.

```yaml
# verify-accept.yaml
- id: text-1a2b3c4d
  reason: Source repeats the title in a header that the template drops.
```

## 3. Numbers are never yours to accept

A dropped or altered figure in a risk whitepaper is a material error.

- Never add a `numbers-*` id to `verify-accept.yaml` yourself, and never fill in
  `approved-by`. The CLI rejects numeric acceptances that do not name a person.
- If the number is wrong in the `.qmd`, fix the `.qmd` (class: fixable).
- Otherwise list it under "needs human review" with both contexts and say what
  a person must check. Only they add the entry, with their own name:

```yaml
- id: numbers-missing-12.25%
  reason: Source figure superseded by the Q3 restatement.
  approved-by: Jane Smith
```

## 4. Report back

Finish with: the overall result, a count per class, each fixable item with its
edit, and each item needing human review with the question to answer. Do not
describe a paper as verified while any hard check is failing.
