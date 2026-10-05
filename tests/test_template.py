from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from galley import __version__, template
from galley.template import Replacement, TemplateConfig, TemplateError

from .conftest import ROOT

MAIN = (
    "\\documentclass{demo}\n"
    "\\title{Old Title}\n"
    "\\begin{document}\n"
    "\\maketitle\n"
    "Costs \\$5 and $x$.\n"
    "\\section{One}\n"
    "Body text.\n"
    "\\end{document}\n"
)


def make_config(**overrides: object) -> TemplateConfig:
    base = TemplateConfig(
        name="demo",
        source=Path("template"),
        main="demo.tex",
        resources=("demo.cls",),
        engine="pdflatex",
        cite_method="citeproc",
        tex_packages=(),
        replacements=(
            Replacement("\\title{Old Title}", "\\title{Old Title}", "\\title{$title$}"),
            Replacement("\\section{One}", "Body text.", "$body$"),
        ),
        glue_before="\\begin{document}",
        environments={"keyfinding": {"env": "keyfinding"}},
        tables={"font-size": "small"},
        format_options={},
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_escape_template_doubles_dollars() -> None:
    assert template.escape_template("\\$5 and $x$") == "\\$$5 and $$x$$"


def test_build_replaces_match_and_range() -> None:
    out = template.build_pandoc_template(MAIN, make_config())
    assert "\\title{$title$}" in out
    assert "Old Title" not in out
    assert "$body$\n\\end{document}" in out
    assert "Body text." not in out


def test_build_escapes_text_outside_replacements() -> None:
    out = template.build_pandoc_template(MAIN, make_config())
    assert "Costs \\$$5 and $$x$$." in out


def test_build_inserts_glue_once_before_anchor() -> None:
    out = template.build_pandoc_template(MAIN, make_config())
    assert out.count(template.GLUE_START) == 1
    assert out.count(template.GLUE_END) == 1
    assert out.index(template.GLUE_END) < out.index("\\begin{document}")
    assert out.index("\\title{$title$}") < out.index(template.GLUE_START)


def test_build_only_adds_glue_and_replacements() -> None:
    config = make_config(replacements=())
    out = template.build_pandoc_template(MAIN, config)
    start = out.index(template.GLUE_START)
    end = out.index(template.GLUE_END) + len(template.GLUE_END) + 1
    assert out[:start] + out[end:] == template.escape_template(MAIN)


@pytest.mark.parametrize("anchor", ["\\title{Missing}", "\n"])
def test_build_rejects_anchor_not_found_exactly_once(anchor: str) -> None:
    config = make_config(replacements=(Replacement(anchor, anchor, "x"),))
    with pytest.raises(TemplateError, match="exactly once"):
        template.build_pandoc_template(MAIN, config)


def test_build_rejects_overlapping_replacements() -> None:
    config = make_config(
        replacements=(
            Replacement("\\title{Old Title}", "\\maketitle", "a"),
            Replacement("\\begin{document}", "\\begin{document}", "b"),
        )
    )
    with pytest.raises(TemplateError, match="overlap"):
        template.build_pandoc_template(MAIN, config)


def test_build_rejects_reversed_range() -> None:
    config = make_config(replacements=(Replacement("\\maketitle", "\\title{Old Title}", "x"),))
    with pytest.raises(TemplateError, match="precedes"):
        template.build_pandoc_template(MAIN, config)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "template.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = (
    "name: demo\nsource: template\nmain: demo.tex\nengine: lualatex\n"
    "glue-before: 'x'\nreplacements:\n  - match: a\n    with: b\n"
    "  - from: c\n    to: d\n    with: e\n"
)


def test_load_config_parses_replacements_and_defaults(tmp_path: Path) -> None:
    config = template.load_config(write_config(tmp_path, VALID))
    assert config.engine == "lualatex"
    assert config.cite_method == "citeproc"
    assert config.replacements == (Replacement("a", "a", "b"), Replacement("c", "d", "e"))
    assert config.environments == {}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (VALID.replace("engine: lualatex", "engine: tex"), "engine must be"),
        (VALID.replace("name: demo\n", ""), "missing required key 'name'"),
        (VALID + "cite-method: bibtex\n", "cite-method must be"),
        (VALID.replace("  - match: a\n", "  - "), "needs 'match' or 'from'/'to'"),
    ],
)
def test_load_config_rejects_invalid(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        template.load_config(write_config(tmp_path, text))


def test_adopt_then_check_is_clean_and_detects_drift(tmp_path: Path) -> None:
    source = tmp_path / "template"
    source.mkdir()
    (source / "demo.tex").write_text(MAIN, encoding="utf-8")
    (source / "demo.cls").write_bytes(b"\\ProvidesClass{demo}\n")
    filters = tmp_path / template.EXTENSION_DIR / "filters"
    filters.mkdir(parents=True)
    for name in (*template.FILTERS, template.POST_RENDER_FILTER):
        (tmp_path / template.EXTENSION_DIR / name).write_text("return {}\n", encoding="utf-8")
    config = make_config()

    written = template.adopt(config, tmp_path, "1.0.0")

    extension = tmp_path / template.EXTENSION_DIR
    assert {p.name for p in written} == {"galley.tex", "_extension.yml", "demo.cls"}
    assert template.sha256(extension / "demo.cls") == template.sha256(source / "demo.cls")
    assert template.check(config, tmp_path, "1.0.0") == []

    (source / "demo.cls").write_bytes(b"\\ProvidesClass{demo}[changed]\n")
    assert template.check(config, tmp_path, "1.0.0") == [
        f"out of date: {template.EXTENSION_DIR / 'demo.cls'}"
    ]


def test_adopt_reports_missing_resource(tmp_path: Path) -> None:
    (tmp_path / "template").mkdir()
    (tmp_path / "template" / "demo.tex").write_text(MAIN, encoding="utf-8")
    with pytest.raises(TemplateError, match="resource not found"):
        template.adopt(make_config(), tmp_path, "1.0.0")


def test_extension_yml_declares_format(config: TemplateConfig) -> None:
    import yaml

    doc = yaml.safe_load(template.render_extension_yml(config, "1.0.0"))
    pdf = doc["contributes"]["formats"]["pdf"]
    assert pdf["template"] == "galley.tex"
    assert pdf["keep-tex"] is True
    assert pdf["pdf-engine"] == config.engine
    assert pdf["cite-method"] == config.cite_method
    assert pdf["format-resources"] == list(config.resources)
    assert pdf["galley"]["environments"] == config.environments


def test_repo_extension_is_up_to_date(config: TemplateConfig) -> None:
    """The committed extension matches a fresh adoption of template/."""
    assert template.check(config, ROOT, __version__) == []


def test_repo_resources_are_byte_identical(config: TemplateConfig) -> None:
    for name in config.resources:
        vendored = ROOT / template.EXTENSION_DIR / name
        assert template.sha256(vendored) == template.sha256(ROOT / config.source / name)
