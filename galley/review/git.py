"""The handful of git operations the review app performs on the paper repo."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """A git command failed; the message is safe to show to the user."""


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def current_branch(repo: Path) -> str:
    return run(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def head_sha(repo: Path) -> str:
    return run(repo, "rev-parse", "HEAD").strip()


def is_dirty(repo: Path, path: str | None = None) -> bool:
    args = ["status", "--porcelain", "--untracked-files=no"]
    if path:
        args += ["--", path]
    return bool(run(repo, *args).strip())


def file_at(repo: Path, revision: str, path: str) -> str | None:
    """The content of ``path`` at ``revision``, or None if it does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"], cwd=repo, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def commit_and_push(repo: Path, paths: list[str], message: str) -> str:
    """Commit ``paths`` and push to the branch's upstream. Returns a line for the user."""
    run(repo, "add", "--", *paths)
    if not run(repo, "diff", "--cached", "--name-only").strip():
        return "Nothing to commit."
    run(repo, "commit", "-m", message)
    try:
        run(repo, "push")
    except GitError as exc:
        raise GitError(
            "Committed locally, but the push was rejected because the branch has new commits "
            "on GitHub. Pull and merge them, then push again.\n" + str(exc)
        ) from exc
    return f"Committed and pushed: {message}"


def pull(repo: Path) -> str:
    """Merge the remote branch. A conflict is left for a normal git merge."""
    try:
        run(repo, "pull", "--no-rebase")
    except GitError as exc:
        conflicted = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo, capture_output=True, text=True, check=False,
        ).stdout.split()  # fmt: skip
        if conflicted:
            raise GitError(
                "The pull hit merge conflicts in: " + ", ".join(conflicted) + ". Resolve them "
                "in your editor, then `git add` the files and `git commit` to finish the merge."
            ) from exc
        raise GitError(
            "Could not pull: " + str(exc) + "\nCommit or stash your local changes first; "
            "nothing was overwritten."
        ) from exc
    return "Pulled the latest commits."
