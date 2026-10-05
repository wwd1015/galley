"""The ``galley`` command line."""

from __future__ import annotations

from pathlib import Path

import typer

from galley import __version__, build, doctor, fidelity, hooks, manifest, scaffold, template
from galley import convert as converter
from galley import verify as verifier
from galley.config import ConfigError, load_paper_config, read_paper_config

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


def _fail(exc: BaseException | str, code: int = 1) -> typer.Exit:
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


@app.command("new")
def new(
    slug: str = typer.Argument(..., help="Paper id: lowercase letters, digits, hyphens."),
    parent: Path = typer.Option(Path("."), "--dir", help="Directory to create the repo in."),
    git: bool = typer.Option(True, "--git/--no-git", help="Run `git init` in the new repo."),
) -> None:
    """Scaffold a paper repo that vendors the galley-pdf extension."""
    try:
        paper_dir = scaffold.new_paper(slug, parent, git=git)
    except (scaffold.ScaffoldError, ConfigError) as exc:
        _fail(exc)
    typer.echo(f"created {paper_dir}")
    typer.echo(f"next: galley build {paper_dir}")


@app.command("build")
def build_command(paper_dir: Path = PAPER_ARGUMENT) -> None:
    """Verify data, render the PDF, run checks and write build-report.json."""
    try:
        result = build.build(paper_dir)
    except (build.BuildError, ConfigError) as exc:
        _fail(exc, 2)
    for problem in result.problems:
        typer.echo(f"problem: {problem}", err=True)
    needs_data = result.report["checks"].get("needs-data-figures", [])
    if needs_data:
        typer.echo(f"note: {len(needs_data)} figure(s) still need data: {', '.join(needs_data)}")
    typer.echo(f"wrote {result.report_path}")
    if result.pdf is not None:
        typer.echo(f"wrote {result.pdf} ({result.report['output']['pages']} pages)")
    if not result.ok:
        raise typer.Exit(1)


@app.command("doctor")
def doctor_command(paper_dir: Path = PAPER_ARGUMENT) -> None:
    """Report tool versions (including the pinned TeX Live) and anything missing."""
    try:
        diagnosis = doctor.diagnose(load_paper_config(paper_dir))
    except ConfigError as exc:
        _fail(exc, 2)
    for line in diagnosis.lines():
        typer.echo(line)
    if not diagnosis.ok:
        raise typer.Exit(1)


@data_app.command("add")
def data_add(
    path: Path = typer.Argument(..., help="File under the paper's data/ directory."),
    paper_dir: Path = typer.Option(Path("."), "--paper", help="Paper repo directory."),
    source: str = typer.Option("", "--source", help="Where the data came from."),
    as_of: str = typer.Option("", "--as-of", help="As-of date of the data (YYYY-MM-DD)."),
) -> None:
    """Record a data file's SHA-256, source and as-of date in the manifest."""
    try:
        entry = manifest.add(paper_dir, path, source=source, as_of=as_of)
    except manifest.ManifestError as exc:
        _fail(exc)
    typer.echo(f"{entry.path}  {entry.sha256}")


@data_app.command("verify")
def data_verify(paper_dir: Path = PAPER_ARGUMENT) -> None:
    """Fail if any file under data/ disagrees with the manifest."""
    try:
        problems = manifest.verify(paper_dir)
    except manifest.ManifestError as exc:
        _fail(exc, 2)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("data matches the manifest")


@app.command("verify")
def verify_command(
    source: Path = typer.Argument(..., help="The original document (.docx or .pdf)."),
    paper_dir: Path = PAPER_ARGUMENT,
    rebuild: bool = typer.Option(
        True, "--build/--no-build", help="Build the paper first (default) or use the existing PDF."
    ),
    settings_file: Path | None = typer.Option(None, "--config", help="Path to a verify.yaml."),
) -> None:
    """Compare the original document with the rendered PDF; fail on content loss."""
    try:
        settings = verifier.load_settings(settings_file) if settings_file else None
        report = verifier.verify(source, paper_dir, rebuild=rebuild, settings=settings)
    except (verifier.VerifyError, ConfigError) as exc:
        _fail(exc, 2)
    for check in report["checks"]:
        typer.echo(f"{check['status'].upper():<9} {check['title']}: {check['summary']}")
    typer.echo(f"wrote {paper_dir / 'verify-report.md'}")
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("hooks")
def hooks_command(paper_dir: Path = PAPER_ARGUMENT) -> None:
    """Install a pre-commit hook that runs `galley verify` on a converted paper."""
    try:
        path = hooks.install(paper_dir)
    except hooks.HookError as exc:
        _fail(exc)
    typer.echo(f"installed {path}")


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
) -> None:
    """Convert a Word, Google Docs or PDF whitepaper into a Galley paper repo."""
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
    typer.echo(f"converted to {out}")
    typer.echo(
        f"tables {len(conversion.tables)}, charts rebuilt {len(conversion.charts)}, "
        f"figures needing data {len(conversion.needs_data)}, "
        f"unmapped styles {len(conversion.unmapped_styles)}, "
        f"unresolved citations {len(conversion.unresolved_citations)}"
    )
    typer.echo(f"wrote {out / converter.REPORT}")
    if report is None:
        typer.echo("verify: not run")
        raise typer.Exit(0 if not run_verify else 1)
    typer.echo(f"verify: {report['status'].upper()}")
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("review")
def review_command(
    paper_dir: Path = PAPER_ARGUMENT,
    pr: int | None = typer.Option(None, "--pr", help="Pull request number to open."),
    port: int = typer.Option(8050, "--port", help="Local port."),
    hostname: str | None = typer.Option(
        None, "--hostname", help="GitHub Enterprise host (default: github.com)."
    ),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the app in a browser."),
) -> None:
    """Review a paper's pull request locally, with a live typeset preview."""
    import webbrowser

    from galley.gh import Gh, GhError
    from galley.review import app as review_app

    try:
        dash_app, _ = review_app.build(paper_dir, Gh(paper_dir.resolve(), hostname), pr=pr)
    except (GhError, ConfigError) as exc:
        _fail(exc, 2)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Galley Review is running at {url} (Ctrl+C to stop)")
    if browser:
        webbrowser.open(url)
    # Bound to the loopback interface only: the app acts with your GitHub credentials.
    dash_app.run(host="127.0.0.1", port=port, debug=False)
