"""Where each paper in a workspace stands: built, verified, converted, in Git."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from galley import build
from galley.template import EXTENSION_DIR, PAPER_CONFIG


def is_paper(directory: Path) -> bool:
    return (directory / EXTENSION_DIR / PAPER_CONFIG).is_file()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def original(directory: Path) -> Path | None:
    """The source document a converted paper keeps in ``source/``."""
    return next(iter(sorted((directory / "source").glob("original.*"))), None)


def paper_dirs(workspace: Path) -> list[Path]:
    """Papers under ``workspace``; a workspace that is itself a paper lists just that."""
    if is_paper(workspace):
        return [workspace]
    if not workspace.is_dir():
        return []
    return sorted(d for d in workspace.iterdir() if d.is_dir() and is_paper(d))


def paper_summary(directory: Path) -> dict[str, Any]:
    document = build.main_document(directory)
    report = read_json(directory / build.REPORT)
    verify = read_json(directory / "verify-report.json")
    source = original(directory)
    return {
        "slug": directory.name,
        "document": document,
        "has_pdf": (directory / Path(document).with_suffix(".pdf").name).is_file(),
        "build_ok": report.get("ok") if report else None,
        "verify": verify.get("status") if verify else None,
        "source": source.name if source else None,
        "git": (directory / ".git").exists(),
    }
