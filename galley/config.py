"""Locate the vendored extension and read the template settings it carries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from galley.template import EXTENSION_DIR, PAPER_CONFIG


class ConfigError(Exception):
    """The paper or tool configuration could not be found or read."""


def extension_source() -> Path:
    """The extension to vendor into papers: packaged copy, else the repo's own."""
    packaged = Path(__file__).resolve().parent / "_extension"
    if packaged.is_dir():
        return packaged
    repo = Path(__file__).resolve().parent.parent / EXTENSION_DIR
    if repo.is_dir():
        return repo
    raise ConfigError("the galley-pdf extension is not available in this installation")


def tool_config_dir() -> Path:
    """Directory holding verify.yaml and style-map.yaml defaults."""
    packaged = Path(__file__).resolve().parent / "_config"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent / "config"


def find_paper_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` to the directory that vendors the extension."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / EXTENSION_DIR / PAPER_CONFIG).is_file():
            return candidate
    return None


@dataclass(frozen=True)
class PaperConfig:
    """Template settings from ``galley.yml`` in the vendored extension."""

    template: str = "unknown"
    engine: str = "xelatex"
    cite_method: str = "citeproc"
    tex_packages: tuple[str, ...] = ()
    tables: dict[str, Any] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    scaffold_metadata: dict[str, Any] = field(default_factory=dict)


def read_paper_config(path: Path) -> PaperConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a YAML mapping")
    return PaperConfig(
        template=str(raw.get("template", "unknown")),
        engine=str(raw.get("engine", "xelatex")),
        cite_method=str(raw.get("cite-method", "citeproc")),
        tex_packages=tuple(str(p) for p in raw.get("tex-packages") or ()),
        tables=dict(raw.get("tables") or {}),
        numbers=dict(raw.get("numbers") or {}),
        style=dict(raw.get("style") or {}),
        scaffold_metadata=dict(raw.get("scaffold-metadata") or {}),
    )


def load_paper_config(start: Path | None = None) -> PaperConfig:
    """Settings for the paper containing ``start``, else the tool's own extension."""
    root = find_paper_root(start)
    path = (root / EXTENSION_DIR if root else extension_source()) / PAPER_CONFIG
    if not path.is_file():
        raise ConfigError(f"{path} not found; run `galley template adopt`")
    return read_paper_config(path)
