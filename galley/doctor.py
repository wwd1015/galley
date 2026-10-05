"""``galley doctor``: report the toolchain and what is missing from it."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from galley import tools
from galley.config import PaperConfig
from galley.fidelity import find_tool


@dataclass
class Diagnosis:
    versions: dict[str, str]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> list[str]:
        out = [f"{name:<10} {value or 'NOT FOUND'}" for name, value in self.versions.items()]
        out.extend(f"problem: {problem}" for problem in self.problems)
        out.append("ok" if self.ok else f"{len(self.problems)} problem(s)")
        return out


def diagnose(config: PaperConfig) -> Diagnosis:
    versions = tools.tool_versions(config.engine)
    for name in ("uv", "gh", "git"):
        binary = shutil.which(name)
        versions[name] = tools.first_line(tools.run([binary, "--version"])) if binary else ""
    problems: list[str] = []
    if not versions["quarto"]:
        problems.append("quarto is not installed (https://quarto.org)")
    if find_tool(config.engine) is None:
        problems.append(f"{config.engine} not found; run `quarto install tinytex`")
    else:
        installed = tools.installed_tex_packages()
        if installed is not None:
            missing = [p for p in config.tex_packages if p not in installed]
            if missing:
                problems.append("missing TeX packages: tlmgr install " + " ".join(missing))
    if config.cite_method == "biblatex" and find_tool("biber") is None:
        problems.append("the template uses biblatex but biber is not installed")
    if not versions["gh"]:
        problems.append("gh is not installed; `galley review` needs it (https://cli.github.com)")
    return Diagnosis(versions=versions, problems=problems)
