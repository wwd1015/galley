"""Find and describe the external tools Galley drives (Quarto, TeX, git)."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from galley import __version__
from galley.fidelity import find_tool


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    """Run a command and return stdout, or an empty string if it fails."""
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def render_env(engine: str) -> dict[str, str]:
    """Environment for Quarto: this Python as the Jupyter host and TeX on PATH."""
    env = dict(os.environ)
    env.setdefault("QUARTO_PYTHON", sys.executable)
    tool = find_tool(engine)
    if tool is not None:
        env["PATH"] = str(tool.parent) + os.pathsep + env.get("PATH", "")
    return env


def tool_versions(engine: str) -> dict[str, str]:
    """Versions of everything that can change the output."""
    engine_bin = find_tool(engine)
    engine_line = first_line(run([str(engine_bin), "--version"])) if engine_bin else ""
    texlive = re.search(r"TeX Live (\d{4})", engine_line)
    quarto = shutil.which("quarto")
    return {
        "galley": __version__,
        "python": platform.python_version(),
        "quarto": first_line(run([quarto, "--version"])) if quarto else "",
        "pandoc": first_line(run([quarto, "pandoc", "--version"])) if quarto else "",
        engine: engine_line,
        "texlive": texlive.group(1) if texlive else "",
    }


def git_sha(directory: Path) -> str | None:
    sha = run(["git", "rev-parse", "HEAD"], cwd=directory).strip()
    return sha or None


def installed_tex_packages() -> set[str] | None:
    """Names of installed TeX Live packages, or None if tlmgr is unavailable."""
    tlmgr = find_tool("tlmgr")
    if tlmgr is None:
        return None
    listing = run([str(tlmgr), "info", "--only-installed", "--data", "name"])
    return {line.strip() for line in listing.splitlines() if line.strip()} or None
