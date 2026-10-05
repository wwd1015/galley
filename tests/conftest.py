"""Shared fixtures and markers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from galley import fidelity, template

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def config() -> template.TemplateConfig:
    return template.load_config(ROOT / "config" / "template.yaml")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    has_quarto = shutil.which("quarto") is not None
    for item in items:
        if "quarto" in item.keywords and not has_quarto:
            item.add_marker(pytest.mark.skip(reason="quarto is not installed"))
        if "tex" in item.keywords:
            engine = template.load_config(ROOT / "config" / "template.yaml").engine
            if not has_quarto or fidelity.find_tool(engine) is None:
                item.add_marker(pytest.mark.skip(reason=f"quarto or {engine} is not installed"))
