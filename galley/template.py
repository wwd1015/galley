"""Adopt a team TeX template as the ``galley-pdf`` Quarto format extension.

Nothing here knows about a particular template. ``config/template.yaml``
describes the team's files and how their sample ``.tex`` maps onto Pandoc
variables; :func:`adopt` generates the extension from that description.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

EXTENSION_DIR = Path("_extensions/galley/galley")
PANDOC_TEMPLATE = "galley.tex"
FILTERS = ("filters/galley-divs.lua", "filters/galley-tables.lua", "filters/galley-review.lua")
POST_RENDER_FILTER = "filters/galley-latex.lua"
ENGINES = ("pdflatex", "xelatex", "lualatex")
CITE_METHODS = ("biblatex", "natbib", "citeproc")
GLUE_START = "% --- galley glue ---"
GLUE_END = "% --- end galley glue ---"


class TemplateError(Exception):
    """The template config is invalid or does not match the team's files."""


@dataclass(frozen=True)
class Replacement:
    """Replace the text from the start of ``start`` through the end of ``end``."""

    start: str
    end: str
    replacement: str


@dataclass(frozen=True)
class TemplateConfig:
    name: str
    source: Path
    main: str
    resources: tuple[str, ...]
    engine: str
    cite_method: str
    tex_packages: tuple[str, ...]
    replacements: tuple[Replacement, ...]
    glue_before: str
    environments: dict[str, dict[str, str]]
    tables: dict[str, str]
    format_options: dict[str, Any]


def _require(raw: dict[str, Any], key: str) -> Any:
    if key not in raw:
        raise TemplateError(f"template config is missing required key '{key}'")
    return raw[key]


def _parse_replacement(item: dict[str, Any]) -> Replacement:
    replacement = str(_require(item, "with"))
    if "match" in item:
        return Replacement(str(item["match"]), str(item["match"]), replacement)
    if "from" in item and "to" in item:
        return Replacement(str(item["from"]), str(item["to"]), replacement)
    raise TemplateError(f"replacement needs 'match' or 'from'/'to': {item!r}")


def load_config(path: Path) -> TemplateConfig:
    """Read and validate a template config. ``source`` is relative to the repo root."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TemplateError(f"{path} is not a YAML mapping")
    engine = str(_require(raw, "engine"))
    if engine not in ENGINES:
        raise TemplateError(f"engine must be one of {ENGINES}, got '{engine}'")
    cite_method = str(raw.get("cite-method", "citeproc"))
    if cite_method not in CITE_METHODS:
        raise TemplateError(f"cite-method must be one of {CITE_METHODS}, got '{cite_method}'")
    return TemplateConfig(
        name=str(_require(raw, "name")),
        source=Path(str(_require(raw, "source"))),
        main=str(_require(raw, "main")),
        resources=tuple(str(r) for r in raw.get("resources", [])),
        engine=engine,
        cite_method=cite_method,
        tex_packages=tuple(str(p) for p in raw.get("tex-packages", [])),
        replacements=tuple(_parse_replacement(r) for r in _require(raw, "replacements")),
        glue_before=str(_require(raw, "glue-before")),
        environments={
            str(cls): {str(k): str(v) for k, v in spec.items()}
            for cls, spec in (raw.get("environments") or {}).items()
        },
        tables={str(k): str(v) for k, v in (raw.get("tables") or {}).items()},
        format_options=dict(raw.get("format-options") or {}),
    )


def escape_template(text: str) -> str:
    """Escape literal TeX so Pandoc's template engine reproduces it verbatim."""
    return text.replace("$", "$$")


def glue_block() -> str:
    return resources.files("galley").joinpath("data/glue.tex").read_text(encoding="utf-8")


def _find_once(text: str, anchor: str) -> int:
    count = text.count(anchor)
    if count != 1:
        raise TemplateError(f"anchor must occur exactly once, found {count} times: {anchor!r}")
    return text.index(anchor)


def build_pandoc_template(main_text: str, config: TemplateConfig) -> str:
    """Turn the team's sample ``.tex`` into a full Pandoc template."""
    # (start, end, raw template text) spans over the original text.
    spans: list[tuple[int, int, str]] = []
    for rep in config.replacements:
        start = _find_once(main_text, rep.start)
        end = start + len(rep.start)
        if rep.end != rep.start:
            end_at = _find_once(main_text, rep.end)
            if end_at < end:
                raise TemplateError(f"'to' anchor precedes 'from' anchor: {rep.end!r}")
            end = end_at + len(rep.end)
        spans.append((start, end, rep.replacement))
    glue_at = _find_once(main_text, config.glue_before)
    spans.append((glue_at, glue_at, glue_block().rstrip("\n") + "\n"))
    spans.sort(key=lambda s: (s[0], s[1]))

    out: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        if start < cursor:
            raise TemplateError(f"replacements overlap near: {main_text[start : start + 40]!r}")
        out.append(escape_template(main_text[cursor:start]))
        out.append(replacement)
        cursor = end
    out.append(escape_template(main_text[cursor:]))
    return "".join(out)


def render_extension_yml(config: TemplateConfig, version: str) -> str:
    """The ``_extension.yml`` declaring the ``galley-pdf`` format."""
    pdf: dict[str, Any] = {
        **config.format_options,
        "template": PANDOC_TEMPLATE,
        "pdf-engine": config.engine,
        "cite-method": config.cite_method,
        "keep-tex": True,
        "fig-format": "pdf",
        "filters": [*FILTERS, {"at": "post-render", "path": POST_RENDER_FILTER}],
        "format-resources": list(config.resources),
        "galley": {
            "template": config.name,
            "environments": config.environments,
            "tables": config.tables,
        },
    }
    doc = {
        "title": "Galley",
        "author": "Galley",
        "version": version,
        "quarto-required": ">=1.4.0",
        "contributes": {"formats": {"pdf": pdf}},
    }
    header = "# Generated by `galley template adopt` from config/template.yaml. Do not edit.\n"
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated(config: TemplateConfig, root: Path, version: str) -> dict[Path, bytes]:
    """Every file adoption owns, keyed by path relative to the extension directory."""
    source = root / config.source
    main = source / config.main
    if not main.is_file():
        raise TemplateError(f"template main file not found: {main}")
    files: dict[Path, bytes] = {
        Path(PANDOC_TEMPLATE): build_pandoc_template(
            main.read_text(encoding="utf-8"), config
        ).encode(),
        Path("_extension.yml"): render_extension_yml(config, version).encode(),
    }
    for name in config.resources:
        resource = source / name
        if not resource.is_file():
            raise TemplateError(f"template resource not found: {resource}")
        files[Path(name)] = resource.read_bytes()
    return files


def adopt(config: TemplateConfig, root: Path, version: str) -> list[Path]:
    """Write the generated extension files under ``root``; return what was written."""
    written: list[Path] = []
    for rel, content in _generated(config, root, version).items():
        target = root / EXTENSION_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(target)
    return written


def check(config: TemplateConfig, root: Path, version: str) -> list[str]:
    """Describe every way the extension on disk differs from a fresh adoption."""
    problems: list[str] = []
    for rel, content in _generated(config, root, version).items():
        target = root / EXTENSION_DIR / rel
        if not target.is_file():
            problems.append(f"missing: {EXTENSION_DIR / rel}")
        elif target.read_bytes() != content:
            problems.append(f"out of date: {EXTENSION_DIR / rel}")
    for rel_filter in (*FILTERS, POST_RENDER_FILTER):
        if not (root / EXTENSION_DIR / rel_filter).is_file():
            problems.append(f"missing: {EXTENSION_DIR / rel_filter}")
    return problems
