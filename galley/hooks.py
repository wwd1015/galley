"""Git hooks for paper repos."""

from __future__ import annotations

import stat
from pathlib import Path

PRE_COMMIT = """\
#!/bin/sh
# Installed by `galley hooks`. Blocks a commit that loses content from the
# original document. Remove this file to disable.
source_file="$(ls source/original.* 2>/dev/null | head -n 1)"
if [ -z "$source_file" ]; then
  exit 0
fi
exec galley verify "$source_file" .
"""


class HookError(Exception):
    """The hook could not be installed."""


def install(paper_dir: Path) -> Path:
    git_dir = paper_dir / ".git"
    if not git_dir.is_dir():
        raise HookError(f"{paper_dir} is not a git repository")
    hook = git_dir / "hooks" / "pre-commit"
    if hook.exists() and "galley hooks" not in hook.read_text(encoding="utf-8"):
        raise HookError(f"{hook} already exists and was not installed by Galley")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(PRE_COMMIT, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook
