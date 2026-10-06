"""The ``galley`` command line."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

from galley import __version__, build, doctor, fidelity, hooks, manifest, scaffold, template
from galley import convert as converter
from galley import status as paper_status
from galley import verify as verifier
from galley.config import ConfigError, load_paper_config, read_paper_config
from galley.verify import report as verify_report

app = typer.Typer(help="Whitepaper build system.", no_args_is_help=True)
template_app = typer.Typer(help="Manage the team TeX template.", no_args_is_help=True)
data_app = typer.Typer(help="Manage a paper's pinned data inputs.", no_args_is_help=True)
app.add_typer(template_app, name="template")
app.add_typer(data_app, name="data")

ROOT_OPTION = typer.Option(Path("."), "--root", help="Galley repo root.")
WORKDIR_OPTION = typer.Option(
    Path(".galley-build"), "--workdir", help="Scratch build directory, relative to the root."
)
CONFIG_OPTION = typer.Option(
    Path("config/template.yaml"), "--config", help="Template config, relative to the root."
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"galley {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    """Whitepaper build system."""


_JSON = False


def _fail(exc: BaseException | str, code: int = 1) -> typer.Exit:
    """Stop with an error: a line on stderr, or in --json mode an object on stdout."""
    if _JSON:
        typer.echo(json.dumps({"ok": False, "error": str(exc), "exit_code": code}, indent=2))
    else:
        typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code)


def _load(root: Path, config: Path) -> template.TemplateConfig:
    try:
        return template.load_config(root / config)
    except (OSError, template.TemplateError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


@template_app.command("adopt")
def template_adopt(root: Path = ROOT_OPTION, config: Path = CONFIG_OPTION) -> None:
    """Generate the galley-pdf extension from the team template."""
    cfg = _load(root, config)
    try:
        written = template.adopt(cfg, root, __version__)
    except template.TemplateError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for path in written:
        typer.echo(f"wrote {path.relative_to(root)}")


@template_app.command("check")
def template_check(root: Path = ROOT_OPTION, config: Path = CONFIG_OPTION) -> None:
    """Fail if the extension is out of date with the team template."""
    cfg = _load(root, config)
    try:
        problems = template.check(cfg, root, __version__)
    except template.TemplateError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        typer.echo("run `galley template adopt` to regenerate", err=True)
        raise typer.Exit(1)
    typer.echo("extension is up to date")


@template_app.command("packages")
def template_packages(
    root: Path = ROOT_OPTION,
    config: Path = CONFIG_OPTION,
    paper: Path | None = typer.Option(
        None, "--paper", help="Read the packages from a paper repo's vendored extension."
    ),
) -> None:
    """Print the TeX Live packages the template needs, for `tlmgr install`."""
    if paper is not None:
        path = paper / template.EXTENSION_DIR / template.PAPER_CONFIG
        try:
            typer.echo(" ".join(read_paper_config(path).tex_packages))
        except (OSError, ConfigError) as exc:
            _fail(exc, 2)
        return
    typer.echo(" ".join(_load(root, config).tex_packages))


@template_app.command("reference")
def template_reference(
    root: Path = ROOT_OPTION, config: Path = CONFIG_OPTION, workdir: Path = WORKDIR_OPTION
) -> None:
    """Rebuild tests/golden/reference.pdf from reference.tex and the team's files."""
    cfg = _load(root, config)
    try:
        built = fidelity.build_reference(root, cfg, root / workdir / "reference")
    except fidelity.FidelityError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    target = root / fidelity.GOLDEN_DIR / fidelity.REFERENCE_PDF
    target.write_bytes(built.read_bytes())
    typer.echo(f"wrote {target.relative_to(root)}")


@template_app.command("verify")
def template_verify(
    root: Path = ROOT_OPTION, config: Path = CONFIG_OPTION, workdir: Path = WORKDIR_OPTION
) -> None:
    """Render the golden document and compare its pages with the reference PDF."""
    cfg = _load(root, config)
    thresholds = fidelity.load_thresholds(root / "config/fidelity.yaml")
    try:
        candidate = fidelity.render_golden(root, cfg, root / workdir / "golden")
    except fidelity.FidelityError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    result = fidelity.compare_pdfs(
        candidate,
        root / fidelity.GOLDEN_DIR / fidelity.REFERENCE_PDF,
        thresholds,
        root / workdir / "diff",
    )
    typer.echo(result.summary())
    for image in result.diff_images:
        typer.echo(f"diff image: {image.relative_to(root)}")
    if not result.passed:
        raise typer.Exit(1)


PAPER_ARGUMENT = typer.Argument(Path("."), help="Paper repo directory.")
JSON_OPTION = typer.Option(
    False,
    "--json",
    help="Print one JSON object on stdout instead of text (for scripts and agents).",
)


def _emit(as_json: bool, payload: dict[str, Any], lines: list[str]) -> None:
    """Print a command's result: one JSON object, or the lines a person reads."""
    global _JSON
    _JSON = as_json
    if as_json:
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        for line in lines:
            typer.echo(line)


def _json_mode(as_json: bool) -> None:
    """Make errors raised from here on come out as JSON too."""
    global _JSON
    _JSON = as_json


@app.command("new")
def new(
    slug: str = typer.Argument(..., help="Paper id: lowercase letters, digits, hyphens."),
    parent: Path = typer.Option(Path("."), "--dir", help="Directory to create the repo in."),
    git: bool = typer.Option(True, "--git/--no-git", help="Run `git init` in the new repo."),
    as_json: bool = JSON_OPTION,
) -> None:
    """Scaffold a paper repo that vendors the galley-pdf extension."""
    _json_mode(as_json)
    try:
        paper_dir = scaffold.new_paper(slug, parent, git=git)
    except (scaffold.ScaffoldError, ConfigError) as exc:
        _fail(exc)
    _emit(
        as_json,
        {"ok": True, "paper": str(paper_dir.resolve()), "slug": slug},
        [f"created {paper_dir}", f"next: galley build {paper_dir}"],
    )


@app.command("status")
def status_command(
    workspace: Path = typer.Argument(Path("."), help="A paper repo, or a folder of paper repos."),
    as_json: bool = JSON_OPTION,
) -> None:
    """Where each paper stands: built, verified, converted from an original, in Git."""
    _json_mode(as_json)
    papers = [paper_status.paper_summary(d) for d in paper_status.paper_dirs(workspace.resolve())]
    lines = [
        f"{p['slug']}: build {_word(p['build_ok'])}, verify {p['verify'] or 'not run'}, "
        f"{'converted from ' + p['source'] if p['source'] else 'written in Galley'}"
        for p in papers
    ] or [f"no Galley papers in {workspace}"]
    _emit(as_json, {"ok": True, "workspace": str(workspace.resolve()), "papers": papers}, lines)


def _word(value: bool | None) -> str:
    return "not run" if value is None else ("pass" if value else "fail")


@app.command("build")
def build_command(paper_dir: Path = PAPER_ARGUMENT, as_json: bool = JSON_OPTION) -> None:
    """Verify data, render the PDF, run checks and write build-report.json."""
    _json_mode(as_json)
    try:
        result = build.build(paper_dir)
    except (build.BuildError, ConfigError) as exc:
        _fail(exc, 2)
    needs_data = result.report["checks"].get("needs-data-figures", [])
    lines = [f"problem: {problem}" for problem in result.problems]
    if needs_data:
        lines.append(f"note: {len(needs_data)} figure(s) still need data: {', '.join(needs_data)}")
    lines.append(f"wrote {result.report_path}")
    if result.pdf is not None:
        lines.append(f"wrote {result.pdf} ({result.report['output']['pages']} pages)")
    _emit(
        as_json,
        {
            "ok": result.ok,
            "problems": result.problems,
            "pdf": str(result.pdf) if result.pdf else None,
            "report_path": str(result.report_path),
            "report": result.report,
        },
        lines,
    )
    if not result.ok:
        raise typer.Exit(1)


@app.command("doctor")
def doctor_command(paper_dir: Path = PAPER_ARGUMENT, as_json: bool = JSON_OPTION) -> None:
    """Report tool versions (including the pinned TeX Live) and anything missing."""
    _json_mode(as_json)
    try:
        diagnosis = doctor.diagnose(load_paper_config(paper_dir))
    except ConfigError as exc:
        _fail(exc, 2)
    _emit(
        as_json,
        {"ok": diagnosis.ok, "versions": diagnosis.versions, "problems": diagnosis.problems},
        diagnosis.lines(),
    )
    if not diagnosis.ok:
        raise typer.Exit(1)


@data_app.command("add")
def data_add(
    path: Path = typer.Argument(..., help="File under the paper's data/ directory."),
    paper_dir: Path = typer.Option(Path("."), "--paper", help="Paper repo directory."),
    source: str = typer.Option("", "--source", help="Where the data came from."),
    as_of: str = typer.Option("", "--as-of", help="As-of date of the data (YYYY-MM-DD)."),
    as_json: bool = JSON_OPTION,
) -> None:
    """Record a data file's SHA-256, source and as-of date in the manifest."""
    _json_mode(as_json)
    try:
        entry = manifest.add(paper_dir, path, source=source, as_of=as_of)
    except manifest.ManifestError as exc:
        _fail(exc)
    _emit(as_json, {"ok": True, "entry": entry.__dict__}, [f"{entry.path}  {entry.sha256}"])


@data_app.command("verify")
def data_verify(paper_dir: Path = PAPER_ARGUMENT, as_json: bool = JSON_OPTION) -> None:
    """Fail if any file under data/ disagrees with the manifest."""
    _json_mode(as_json)
    try:
        problems = manifest.verify(paper_dir)
    except manifest.ManifestError as exc:
        _fail(exc, 2)
    _emit(
        as_json,
        {"ok": not problems, "problems": problems},
        problems or ["data matches the manifest"],
    )
    if problems:
        raise typer.Exit(1)


@app.command("verify")
def verify_command(
    source: Path = typer.Argument(..., help="The original document (.docx or .pdf)."),
    paper_dir: Path = PAPER_ARGUMENT,
    rebuild: bool = typer.Option(
        True, "--build/--no-build", help="Build the paper first (default) or use the existing PDF."
    ),
    settings_file: Path | None = typer.Option(None, "--config", help="Path to a verify.yaml."),
    as_json: bool = JSON_OPTION,
) -> None:
    """Compare the original document with the rendered PDF; fail on content loss."""
    _json_mode(as_json)
    try:
        settings = verifier.load_settings(settings_file) if settings_file else None
        report = verifier.verify(source, paper_dir, rebuild=rebuild, settings=settings)
    except (verifier.VerifyError, ConfigError) as exc:
        _fail(exc, 2)
    lines = [
        f"{check['status'].upper():<9} {check['title']}: {check['summary']}"
        for check in report["checks"]
    ]
    lines.append(f"wrote {paper_dir / 'verify-report.md'}")
    _emit(as_json, {"ok": report["status"] == "pass", "report": report}, lines)
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("accept")
def accept_command(
    finding: str = typer.Argument(
        ..., help="Finding id from the verify report, e.g. text-1a2b3c4d."
    ),
    paper_dir: Path = PAPER_ARGUMENT,
    reason: str = typer.Option(..., "--reason", help="Why the difference is acceptable."),
    approved_by: str = typer.Option(
        "", "--approved-by", help="The person who checked it. Required for numeric findings."
    ),
    as_json: bool = JSON_OPTION,
) -> None:
    """Record that a person accepts one verify finding (takes effect on the next verify).

    A numeric finding is only accepted when --approved-by names a person; an
    agent must never supply a name on its own.
    """
    _json_mode(as_json)
    try:
        path = verify_report.add_acceptance(paper_dir, finding, reason, approved_by)
    except ValueError as exc:
        _fail(exc)
    _emit(
        as_json,
        {"ok": True, "file": str(path), "id": finding, "next": "run galley verify again"},
        [f"recorded {finding} in {path}", "run `galley verify` again to apply it"],
    )


@app.command("hooks")
def hooks_command(paper_dir: Path = PAPER_ARGUMENT, as_json: bool = JSON_OPTION) -> None:
    """Install a pre-commit hook that runs `galley verify` on a converted paper."""
    _json_mode(as_json)
    try:
        path = hooks.install(paper_dir)
    except hooks.HookError as exc:
        _fail(exc)
    _emit(as_json, {"ok": True, "hook": str(path)}, [f"installed {path}"])


@app.command("convert")
def convert_command(
    source: str = typer.Argument(..., help="A .docx or .pdf file, or a Google Docs URL."),
    out: Path = typer.Option(..., "--out", help="Directory for the new paper repo."),
    bib: Path | None = typer.Option(None, "--bib", help="BibTeX file to match citations against."),
    source_url: str = typer.Option("", "--source-url", help="Where the document lives online."),
    style_map: Path | None = typer.Option(None, "--style-map", help="Path to a style-map.yaml."),
    run_verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Finish by running galley verify (default)."
    ),
    as_json: bool = JSON_OPTION,
) -> None:
    """Convert a Word, Google Docs or PDF whitepaper into a Galley paper repo."""
    _json_mode(as_json)
    try:
        if converter.GOOGLE_DOC.match(source):
            source_url = source_url or source
            path = converter.export_google_doc(source, out.parent / f".{out.name}-export.docx")
        else:
            path = Path(source)
        conversion, report = converter.convert(
            path,
            out,
            bib=bib,
            source_url=source_url,
            style_map_path=style_map,
            run_verify=run_verify,
        )
    except converter.ConvertError as exc:
        _fail(exc, 2)
    verified = report["status"] if report is not None else None
    payload = {
        "ok": verified == "pass" or not run_verify,
        "paper": str(out.resolve()),
        "report_path": str((out / converter.REPORT).resolve()),
        "tables": sorted(conversion.tables),
        "charts_rebuilt": [chart.figure_id for chart in conversion.charts],
        "figures_needing_data": conversion.needs_data,
        "unmapped_styles": dict(conversion.unmapped_styles),
        "unresolved_citations": conversion.unresolved_citations,
        "unsure": conversion.unsure,
        "verify": verified,
        "verify_report": report,
    }
    lines = [
        f"converted to {out}",
        f"tables {len(conversion.tables)}, charts rebuilt {len(conversion.charts)}, "
        f"figures needing data {len(conversion.needs_data)}, "
        f"unmapped styles {len(conversion.unmapped_styles)}, "
        f"unresolved citations {len(conversion.unresolved_citations)}",
        f"wrote {out / converter.REPORT}",
        f"verify: {verified.upper() if verified else 'not run'}",
    ]
    _emit(as_json, payload, lines)
    if not payload["ok"]:
        raise typer.Exit(1)


# -- pull request review from the command line --------------------------------

pr_app = typer.Typer(
    help="Review a paper's pull request from the command line (what the app's Review tab does).",
    no_args_is_help=True,
)
app.add_typer(pr_app, name="pr")

PAPER_OPTION = typer.Option(Path("."), "--paper", help="Paper repo directory.")
PR_OPTION = typer.Option(
    None, "--pr", help="Pull request number (default: the only open one touching a .qmd)."
)
FILE_OPTION = typer.Option(None, "--file", help="The .qmd file (default: the first in the PR).")
GH_HOST_OPTION = typer.Option(None, "--hostname", help="GitHub Enterprise host.")
BODY_OPTION = typer.Option(..., "--body", help="Comment text (Markdown).")


def _session(paper_dir: Path, pr: int | None, file: str | None, hostname: str | None) -> Any:
    """A review session on one pull request, or a clean failure."""
    from galley.gh import Gh
    from galley.review.session import ReviewSession

    # GALLEY_GH points at another `gh` executable (the demo and the tests use a stand-in).
    gh = Gh(paper_dir.resolve(), hostname, os.environ.get("GALLEY_GH", "gh"))
    session = ReviewSession(paper_dir, gh)
    session.start()
    if pr is None:
        if len(session.prs) != 1:
            numbers = ", ".join(f"#{p['number']}" for p in session.prs) or "none"
            raise ValueError(f"pass --pr: open pull requests touching .qmd files: {numbers}")
        pr = int(session.prs[0]["number"])
    session.select_pr(pr)
    if file is not None:
        session.select_file(file)
    return session


def _review(as_json: bool, action: Any, paper_dir: Path, pr: int | None, file: str | None,
            hostname: str | None, done: str) -> None:  # fmt: skip
    """Open a session, run ``action`` on it, and print the threads that result."""
    from galley.gh import GhError
    from galley.review.git import GitError
    from galley.review.session import ReviewError

    _json_mode(as_json)
    try:
        session = _session(paper_dir, pr, file, hostname)
        action(session)
    except (GhError, GitError, ConfigError) as exc:
        _fail(exc, 2)
    except (ReviewError, ValueError) as exc:
        _fail(exc)
    snapshot = session.snapshot()
    threads = snapshot["threads"]
    for thread in threads:
        for comment in thread["comments"]:
            comment.pop("body_html", None)
            comment.pop("avatar", None)
    lines = [done] if done else []
    for thread in threads:
        where = "outdated" if thread["outdated"] else f"line {thread['line']}"
        state = "resolved" if thread["resolved"] else "open"
        lines.append(f"[{thread['id']}] {thread['path']} {where} ({state})")
        lines.extend(f"    {c['author']}: {c['body']}" for c in thread["comments"])
    if not threads:
        lines.append("no comment threads on this file")
    _emit(
        as_json,
        {"ok": True, "pr": snapshot["pr"], "file": snapshot["path"], "files": snapshot["files"],
         "threads": threads},
        lines,
    )  # fmt: skip


@pr_app.command("list")
def pr_list(
    paper_dir: Path = PAPER_OPTION,
    hostname: str | None = GH_HOST_OPTION,
    as_json: bool = JSON_OPTION,
) -> None:
    """Open pull requests that touch a .qmd file."""
    from galley.gh import Gh, GhError

    _json_mode(as_json)
    try:
        prs = Gh(paper_dir.resolve(), hostname, os.environ.get("GALLEY_GH", "gh")).list_prs()
    except GhError as exc:
        _fail(exc, 2)
    lines = [f"#{p['number']} {p['title']} ({p['branch']})" for p in prs] or [
        "no open pull requests"
    ]
    _emit(as_json, {"ok": True, "pull_requests": prs}, lines)


@pr_app.command("threads")
def pr_threads(
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Every comment thread on a file, with the line it sits on now."""
    _review(as_json, lambda session: None, paper_dir, pr, file, hostname, "")


@pr_app.command("comment")
def pr_comment(
    line: int = typer.Option(..., "--line", help="1-based line number in the file."),
    body: str = BODY_OPTION,
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Comment on a line. Lines outside the diff get an anchored file comment automatically."""
    _review(as_json, lambda s: s.add_comment(line, body), paper_dir, pr, file, hostname,
            f"commented on line {line}")  # fmt: skip


@pr_app.command("reply")
def pr_reply(
    thread: int = typer.Argument(..., help="Thread id (the id of its first comment)."),
    body: str = BODY_OPTION,
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Reply in a thread."""
    _review(as_json, lambda s: s.reply(thread, body), paper_dir, pr, file, hostname,
            f"replied in thread {thread}")  # fmt: skip


@pr_app.command("resolve")
def pr_resolve(
    thread: int = typer.Argument(..., help="Thread id."),
    undo: bool = typer.Option(False, "--undo", help="Mark the thread unresolved instead."),
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Resolve a thread (or, with --undo, unresolve it)."""
    _review(as_json, lambda s: s.set_resolved(thread, not undo), paper_dir, pr, file, hostname,
            f"{'unresolved' if undo else 'resolved'} thread {thread}")  # fmt: skip


@pr_app.command("edit")
def pr_edit(
    comment: int = typer.Argument(..., help="Id of one of your own comments."),
    body: str = BODY_OPTION,
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Edit one of your own comments."""
    _review(as_json, lambda s: s.edit_comment(comment, body), paper_dir, pr, file, hostname,
            f"edited comment {comment}")  # fmt: skip


@pr_app.command("delete")
def pr_delete(
    comment: int = typer.Argument(..., help="Id of one of your own comments."),
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION, file: str | None = FILE_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Delete one of your own comments."""
    _review(as_json, lambda s: s.delete_comment(comment), paper_dir, pr, file, hostname,
            f"deleted comment {comment}")  # fmt: skip


@pr_app.command("submit")
def pr_submit(
    event: str = typer.Option(
        "comment", "--event", help="comment, approve or request-changes."
    ),
    body: str = typer.Option("", "--body", help="Review summary."),
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Submit a review: comment, approve or request changes."""
    name = event.upper().replace("-", "_")
    _review(as_json, lambda s: s.submit_review(name, body), paper_dir, pr, None, hostname,
            f"submitted review: {event}")  # fmt: skip


@pr_app.command("push")
def pr_push(
    paper_dir: Path = PAPER_OPTION, pr: int | None = PR_OPTION,
    hostname: str | None = GH_HOST_OPTION, as_json: bool = JSON_OPTION,
) -> None:  # fmt: skip
    """Commit edits to the pull request's .qmd files and push them to its branch."""
    _review(as_json, lambda s: s.commit_and_push(), paper_dir, pr, None, hostname,
            "committed and pushed any edits")  # fmt: skip


@app.command("commands")
def commands_command(as_json: bool = JSON_OPTION) -> None:
    """List every command with its arguments and options (use --json for a machine-readable map)."""
    from typer.main import get_command

    def describe(command: Any, path: str) -> list[dict[str, Any]]:
        children = getattr(command, "commands", None)
        if children:
            found: list[dict[str, Any]] = []
            for name in sorted(children):
                found.extend(describe(children[name], f"{path} {name}".strip()))
            return found
        parameters = []
        for param in command.params:
            if param.name == "help":
                continue
            default = param.default
            if callable(default) or not isinstance(default, str | int | float | bool | type(None)):
                default = None if default is None or callable(default) else str(default)
            parameters.append(
                {
                    "name": param.name,
                    "kind": "argument" if param.param_type_name == "argument" else "option",
                    "flags": list(param.opts) + list(param.secondary_opts),
                    "required": bool(param.required),
                    "default": default,
                    "help": getattr(param, "help", None) or "",
                }
            )
        summary = (command.help or "").strip().splitlines()[0] if command.help else ""
        return [{"command": f"galley {path}", "summary": summary, "parameters": parameters}]

    catalog = describe(get_command(app), "")
    _emit(
        as_json,
        {
            "ok": True,
            "exit_codes": {
                "0": "success",
                "1": "ran, and a check or request failed",
                "2": "could not run (bad input, missing tool, GitHub unreachable)",
            },
            "commands": catalog,
        },
        [f"{entry['command']:<28} {entry['summary']}" for entry in catalog],
    )


def _serve(workspace: Path, *, tab: str, pr: int | None, port: int, hostname: str | None,
           browser: bool, demo: bool = False) -> None:  # fmt: skip
    import webbrowser

    from galley import workbench as wb

    bench = wb.Workbench(workspace, hostname=hostname, demo=demo)
    bench.tab = tab
    if tab == "review":
        if bench.paper_dir is None:
            _fail(f"{workspace} is not a Galley paper repo", 2)
        bench.open_review(pr)
        if bench.review is None:
            _fail(bench.review_error, 2)
    dash_app = wb.create_app(bench)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Galley is running at {url} (Ctrl+C to stop)")
    if browser:
        webbrowser.open(url)
    # Bound to the loopback interface only: the app acts with your GitHub credentials.
    dash_app.run(host="127.0.0.1", port=port, debug=False)


PORT_OPTION = typer.Option(8050, "--port", help="Local port.")
HOSTNAME_OPTION = typer.Option(
    None, "--hostname", help="GitHub Enterprise host (default: github.com)."
)
BROWSER_OPTION = typer.Option(True, "--browser/--no-browser", help="Open the app in a browser.")


@app.command("app")
def app_command(
    workspace: Path = typer.Argument(
        Path("."), help="Folder that holds (or will hold) your paper repos."
    ),
    port: int = PORT_OPTION,
    hostname: str | None = HOSTNAME_OPTION,
    browser: bool = BROWSER_OPTION,
) -> None:
    """Open the Galley app: convert, build, verify and review papers in one window."""
    workspace.mkdir(parents=True, exist_ok=True)
    _serve(workspace, tab="papers", pr=None, port=port, hostname=hostname, browser=browser)


@app.command("demo")
def demo_command(
    workspace: Path = typer.Argument(Path("galley-demo"), help="Folder for the demo's files."),
    port: int = PORT_OPTION,
    browser: bool = BROWSER_OPTION,
) -> None:
    """Walk the whole process on sample material, with a simulated GitHub (nothing is sent)."""
    workspace.mkdir(parents=True, exist_ok=True)
    _serve(workspace, tab="papers", pr=None, port=port, hostname=None, browser=browser, demo=True)


@app.command("review")
def review_command(
    paper_dir: Path = PAPER_ARGUMENT,
    pr: int | None = typer.Option(None, "--pr", help="Pull request number to open."),
    port: int = PORT_OPTION,
    hostname: str | None = HOSTNAME_OPTION,
    browser: bool = BROWSER_OPTION,
) -> None:
    """Open the Galley app on a paper's pull request review, with a live typeset preview."""
    _serve(paper_dir, tab="review", pr=pr, port=port, hostname=hostname, browser=browser)
