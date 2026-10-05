"""The ``galley`` command line."""

from __future__ import annotations

from pathlib import Path

import typer

from galley import __version__, fidelity, template

app = typer.Typer(help="Whitepaper build system.", no_args_is_help=True)
template_app = typer.Typer(help="Manage the team TeX template.", no_args_is_help=True)
app.add_typer(template_app, name="template")

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
def template_packages(root: Path = ROOT_OPTION, config: Path = CONFIG_OPTION) -> None:
    """Print the TeX Live packages the template needs, for `tlmgr install`."""
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
