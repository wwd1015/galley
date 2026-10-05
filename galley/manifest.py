"""``data/manifest.yaml``: every data input pinned by SHA-256."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MANIFEST = Path("data/manifest.yaml")
DATA_DIR = Path("data")


class ManifestError(Exception):
    """The manifest is missing or malformed."""


@dataclass(frozen=True)
class Entry:
    path: str
    sha256: str
    source: str = ""
    as_of: str = ""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(paper_dir: Path) -> list[Entry]:
    path = paper_dir / MANIFEST
    if not path.is_file():
        raise ManifestError(f"{MANIFEST} not found in {paper_dir}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    files = raw.get("files") if isinstance(raw, dict) else None
    if files is None:
        files = []
    if not isinstance(files, list):
        raise ManifestError(f"{MANIFEST}: 'files' must be a list")
    entries: list[Entry] = []
    for item in files:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise ManifestError(f"{MANIFEST}: every entry needs 'path' and 'sha256': {item!r}")
        entries.append(
            Entry(
                path=str(item["path"]),
                sha256=str(item["sha256"]),
                source=str(item.get("source", "")),
                as_of=str(item.get("as-of", "")),
            )
        )
    return entries


def save(paper_dir: Path, entries: list[Entry]) -> None:
    files: list[dict[str, Any]] = [
        {"path": e.path, "sha256": e.sha256, "source": e.source, "as-of": e.as_of}
        for e in sorted(entries, key=lambda e: e.path)
    ]
    header = "# Every file under data/ with its SHA-256. `galley build` fails on any mismatch.\n"
    path = paper_dir / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump({"files": files}, sort_keys=False), encoding="utf-8")


def data_files(paper_dir: Path) -> list[str]:
    """Every file under ``data/`` except the manifest, as POSIX paths from the paper root."""
    base = paper_dir / DATA_DIR
    if not base.is_dir():
        return []
    return sorted(
        p.relative_to(paper_dir).as_posix()
        for p in base.rglob("*")
        if p.is_file() and p.relative_to(paper_dir) != MANIFEST and not p.name.startswith(".")
    )


def verify(paper_dir: Path) -> list[str]:
    """Every way the files under ``data/`` disagree with the manifest."""
    entries = load(paper_dir)
    problems: list[str] = []
    listed = set()
    for entry in entries:
        listed.add(entry.path)
        target = paper_dir / entry.path
        if not target.is_file():
            problems.append(f"missing: {entry.path}")
        elif file_sha256(target) != entry.sha256:
            problems.append(f"hash mismatch: {entry.path}")
    problems.extend(
        f"not in manifest: {path}" for path in data_files(paper_dir) if path not in listed
    )
    return problems


def add(paper_dir: Path, path: Path, source: str = "", as_of: str = "") -> Entry:
    """Record (or refresh) ``path``, which must live under the paper's ``data/``."""
    resolved = (paper_dir / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        relative = resolved.relative_to((paper_dir / DATA_DIR).resolve())
    except ValueError as exc:
        raise ManifestError(f"{path} is not under {DATA_DIR}/") from exc
    if not resolved.is_file():
        raise ManifestError(f"{path} does not exist")
    key = (DATA_DIR / relative).as_posix()
    entries = load(paper_dir) if (paper_dir / MANIFEST).is_file() else []
    previous = next((e for e in entries if e.path == key), None)
    entry = Entry(
        path=key,
        sha256=file_sha256(resolved),
        source=source or (previous.source if previous else ""),
        as_of=as_of or (previous.as_of if previous else ""),
    )
    save(paper_dir, [e for e in entries if e.path != key] + [entry])
    return entry


def hashes(paper_dir: Path) -> dict[str, str]:
    return {entry.path: entry.sha256 for entry in sorted(load(paper_dir), key=lambda e: e.path)}
