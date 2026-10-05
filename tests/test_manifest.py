from __future__ import annotations

from pathlib import Path

import pytest

from galley import manifest
from galley.manifest import ManifestError


def paper(tmp_path: Path) -> Path:
    (tmp_path / "data" / "tables").mkdir(parents=True)
    (tmp_path / "data" / "rates.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    manifest.save(tmp_path, [])
    return tmp_path


def test_add_records_hash_source_and_date(tmp_path: Path) -> None:
    root = paper(tmp_path)
    entry = manifest.add(root, Path("data/rates.csv"), source="Treasury", as_of="2026-09-30")
    assert entry.sha256 == manifest.file_sha256(root / "data" / "rates.csv")
    assert manifest.load(root) == [entry]
    assert (entry.source, entry.as_of) == ("Treasury", "2026-09-30")
    assert manifest.verify(root) == []


def test_readd_refreshes_hash_and_keeps_provenance(tmp_path: Path) -> None:
    root = paper(tmp_path)
    manifest.add(root, Path("data/rates.csv"), source="Treasury", as_of="2026-09-30")
    (root / "data" / "rates.csv").write_text("a,b\n1,3\n", encoding="utf-8")
    assert manifest.verify(root) == ["hash mismatch: data/rates.csv"]
    entry = manifest.add(root, Path("data/rates.csv"))
    assert entry.source == "Treasury"
    assert manifest.verify(root) == []


def test_verify_reports_missing_and_unlisted(tmp_path: Path) -> None:
    root = paper(tmp_path)
    manifest.add(root, Path("data/rates.csv"))
    (root / "data" / "tables" / "new.csv").write_text("x\n", encoding="utf-8")
    (root / "data" / "rates.csv").unlink()
    assert manifest.verify(root) == [
        "missing: data/rates.csv",
        "not in manifest: data/tables/new.csv",
    ]


def test_add_rejects_files_outside_data(tmp_path: Path) -> None:
    root = paper(tmp_path)
    (root / "other.csv").write_text("x\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="not under data"):
        manifest.add(root, Path("other.csv"))
    with pytest.raises(ManifestError, match="does not exist"):
        manifest.add(root, Path("data/nope.csv"))


def test_load_rejects_missing_or_malformed(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        manifest.load(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "manifest.yaml").write_text("files:\n  - path: x\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="'path' and 'sha256'"):
        manifest.load(tmp_path)
