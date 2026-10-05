"""Matplotlib style for exhibits, driven by the adopted template's config."""

from __future__ import annotations

import os
from importlib import resources
from typing import Any

import matplotlib
import matplotlib.style
from cycler import cycler
from matplotlib import backend_bases

from galley.config import PaperConfig, load_paper_config

MPLSTYLE = "data/galley.mplstyle"


def palette(config: PaperConfig | None = None) -> list[str]:
    """The template's chart colours, in order of use."""
    config = config or load_paper_config()
    return [str(colour) for colour in config.style.get("palette") or []]


def _put_tex_on_path(engine: str) -> None:
    from galley.fidelity import find_tool

    tool = find_tool(engine)
    if tool is None:
        raise RuntimeError(f"TeX engine '{engine}' not found; run `galley doctor`")
    directory = str(tool.parent)
    if directory not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


def use(pgf: bool | None = None, config: PaperConfig | None = None) -> None:
    """Apply the Galley chart style.

    With ``pgf`` (default: the template's ``style.pgf`` setting) chart text is
    typeset by the template's TeX engine with its font preamble, so labels use
    exactly the fonts of the surrounding document.
    """
    config = config or load_paper_config()
    settings = config.style
    with resources.as_file(resources.files("galley").joinpath(MPLSTYLE)) as path:
        matplotlib.style.use(str(path))

    params: dict[str, Any] = {}
    if settings.get("font-size"):
        size = float(settings["font-size"])
        params.update({"font.size": size, "axes.titlesize": size + 1, "legend.fontsize": size})
    if settings.get("figure-width") and settings.get("figure-height"):
        params["figure.figsize"] = (
            float(settings["figure-width"]),
            float(settings["figure-height"]),
        )
    if settings.get("line-width"):
        params["lines.linewidth"] = float(settings["line-width"])
    if settings.get("font-family"):
        params["font.family"] = str(settings["font-family"])
    colours = palette(config)
    if colours:
        params["axes.prop_cycle"] = cycler(color=colours)

    if settings.get("pgf", False) if pgf is None else pgf:
        from matplotlib.backends.backend_pgf import FigureCanvasPgf

        _put_tex_on_path(config.engine)
        params.update(
            {
                "pgf.texsystem": config.engine,
                "pgf.rcfonts": False,
                "pgf.preamble": str(settings.get("pgf-preamble", "")),
            }
        )
        # Every PDF figure, including the ones Quarto captures, goes through TeX.
        backend_bases.register_backend("pdf", FigureCanvasPgf)
    matplotlib.rcParams.update(params)  # type: ignore[arg-type]
