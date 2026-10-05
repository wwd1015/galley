"""Thresholds and conventions for verification, from ``verify.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from galley.config import tool_config_dir

DEFAULT_LABELS = [
    "Figure",
    "Fig.",
    "Table",
    "Section",
    "Chapter",
    "Exhibit",
    "Chart",
    "Appendix",
    "Equation",
]


@dataclass(frozen=True)
class VerifySettings:
    min_coverage: float = 0.995
    max_missing_run: int = 8
    extra_numbers_fail: bool = True
    min_caption_similarity: float = 0.98
    labels: list[str] = field(default_factory=lambda: list(DEFAULT_LABELS))
    figure_labels: list[str] = field(default_factory=lambda: ["Figure", "Fig.", "Exhibit", "Chart"])
    table_labels: list[str] = field(default_factory=lambda: ["Table"])
    caption_below: bool = True
    furniture_zone: float = 0.09

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": {"min-coverage": self.min_coverage, "max-missing-run": self.max_missing_run},
            "numbers": {"extra": "fail" if self.extra_numbers_fail else "warn"},
            "exhibits": {"min-caption-similarity": self.min_caption_similarity},
        }


def load_settings(path: Path | None = None) -> VerifySettings:
    """Read ``verify.yaml``: the given file, else the tool's default."""
    path = path or tool_config_dir() / "verify.yaml"
    if not path.is_file():
        return VerifySettings()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = VerifySettings()
    text = raw.get("text") or {}
    return VerifySettings(
        min_coverage=float(text.get("min-coverage", base.min_coverage)),
        max_missing_run=int(text.get("max-missing-run", base.max_missing_run)),
        extra_numbers_fail=str((raw.get("numbers") or {}).get("extra", "fail")) == "fail",
        min_caption_similarity=float(
            (raw.get("exhibits") or {}).get("min-caption-similarity", base.min_caption_similarity)
        ),
        labels=[str(v) for v in raw.get("labels", base.labels)],
        figure_labels=[str(v) for v in raw.get("figure-labels", base.figure_labels)],
        table_labels=[str(v) for v in raw.get("table-labels", base.table_labels)],
        caption_below=str(raw.get("figure-caption-position", "below")) == "below",
        furniture_zone=float(raw.get("furniture-zone", base.furniture_zone)),
    )
