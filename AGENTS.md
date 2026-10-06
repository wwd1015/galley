# Using Galley from another program or AI agent

Everything the app does is available as a command. Nothing prompts for input.

## Conventions

- Add `--json` to any command to get **one JSON object on stdout** and nothing
  else. Every object has `"ok": true|false`. Errors in JSON mode are
  `{"ok": false, "error": "...", "exit_code": n}`.
- Exit codes: `0` success; `1` it ran and a check or request failed; `2` it
  could not run (bad input, missing tool, GitHub unreachable).
- `galley commands --json` lists every command with its arguments, options,
  defaults and help text. Read it instead of guessing flags.
- Paths in results are absolute. Commands take the paper directory as an
  argument or `--paper`; the default is the current directory.

## The usual flow

```sh
galley doctor --json                                  # toolchain ready?
galley convert legacy.docx --out papers/my-paper --bib refs.bib --json
galley status papers --json                           # where every paper stands
galley build papers/my-paper --json
galley verify papers/my-paper/source/original.docx papers/my-paper --json
```

`convert` already builds and verifies; its result carries `verify` and the full
`verify_report`. Exit code 1 from `convert` or `verify` means content differs:
read `report.checks[].findings[]`, each with an `id`, `message`, `source`,
`candidate` and `location`.

To start an empty paper instead: `galley new my-paper --dir papers --json`.
To pin a data file: `galley data add data/rates.csv --paper papers/my-paper --source "Treasury MI" --as-of 2026-09-30 --json`.

## Accepting a verify finding

```sh
galley accept text-1a2b3c4d papers/my-paper --reason "Header repeated in the source" --json
galley verify papers/my-paper/source/original.docx papers/my-paper --json
```

**Never accept a `numbers-*` finding on your own, and never invent a value for
`--approved-by`.** A numeric difference needs a named person who checked it;
the next `verify` rejects anything else and lists it under
`report["rejected-acceptances"]`. Report numeric findings to a person instead.

## Reviewing a pull request

The paper must be a GitHub repository reachable with `gh` (`gh auth status`).

```sh
galley pr list --paper papers/my-paper --json
galley pr threads --paper papers/my-paper --pr 12 --json
galley pr comment --line 42 --body "Source for this figure?" --paper papers/my-paper --pr 12 --json
galley pr reply 1234567 --body "Treasury MI, September." --paper papers/my-paper --pr 12 --json
galley pr resolve 1234567 --paper papers/my-paper --pr 12 --json      # --undo to unresolve
galley pr submit --event request-changes --body "See comments." --paper papers/my-paper --pr 12 --json
galley pr push --paper papers/my-paper --pr 12 --json                 # commit and push .qmd edits
```

Every `pr` command returns the file's threads after the change. A thread's
`id` is the id of its first comment; `line` is where it sits in the current
text (`null` with `"outdated": true` when the commented text is gone). `--pr`
may be left out when exactly one open pull request touches a `.qmd` file.
`--line` works on any line: outside the pull request's diff Galley posts an
anchored file comment, which is how GitHub allows it.

## Things an agent should not do

- Do not edit anything under `source/`; it is the original document.
- Do not delete content to make `verify` pass.
- Do not start `galley app`, `galley demo` or `galley review`: they open a
  browser window for a person and run until stopped.
