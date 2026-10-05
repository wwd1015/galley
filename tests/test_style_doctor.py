from __future__ import annotations

import matplotlib
import pytest

from galley import doctor, style
from galley.config import PaperConfig, load_paper_config


@pytest.fixture(autouse=True)
def restore_rcparams() -> object:
    with matplotlib.rc_context():
        yield


def test_use_applies_config_without_pgf() -> None:
    config = PaperConfig(
        style={
            "palette": ["#112233", "#445566"],
            "font-size": 8,
            "line-width": 2.5,
            "figure-width": 5,
            "figure-height": 2.5,
            "font-family": "serif",
        }
    )
    style.use(pgf=False, config=config)
    params = matplotlib.rcParams
    assert params["axes.prop_cycle"].by_key()["color"] == ["#112233", "#445566"]
    assert params["font.size"] == 8.0
    assert params["lines.linewidth"] == 2.5
    assert list(params["figure.figsize"]) == [5.0, 2.5]
    assert params["axes.spines.top"] is False  # from the base mplstyle


def test_palette_from_repo_template() -> None:
    assert style.palette(load_paper_config())[0] == "#14324F"


@pytest.mark.tex
def test_use_with_pgf_sets_engine_and_preamble() -> None:
    config = load_paper_config()
    style.use(pgf=True, config=config)
    assert matplotlib.rcParams["pgf.texsystem"] == config.engine
    assert "fontspec" in matplotlib.rcParams["pgf.preamble"]


def test_doctor_reports_versions_and_missing_engine() -> None:
    diagnosis = doctor.diagnose(PaperConfig(engine="no-such-engine"))
    assert diagnosis.versions["python"]
    assert any("no-such-engine not found" in p for p in diagnosis.problems)
    assert diagnosis.lines()[-1].endswith("problem(s)")


@pytest.mark.tex
def test_doctor_is_clean_for_repo_template() -> None:
    diagnosis = doctor.diagnose(load_paper_config())
    assert diagnosis.versions["texlive"]
    assert not [p for p in diagnosis.problems if "TeX" in p or "not found" in p], diagnosis.problems
