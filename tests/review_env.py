"""A paper repo with a pull request, two reviewers' clones and a fake GitHub."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from galley.gh import Gh
from galley.review.session import ReviewSession

from . import builders

FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
EDITED_LINE = "Retail balances fell by 4.5% while wholesale balances fell by 12.25%."
NEW_LINE = "Retail balances fell by 4.8% while wholesale balances fell by 12.25%."


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return result.stdout


@dataclass
class ReviewEnv:
    state: Path
    alice_dir: Path
    bob_dir: Path
    alice: Gh
    bob: Gh

    def session(self, who: str) -> ReviewSession:
        gh, repo = (self.alice, self.alice_dir) if who == "alice" else (self.bob, self.bob_dir)
        session = ReviewSession(repo, gh)
        session.start()
        return session

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def publish_head(self, repo: Path) -> None:
        """Tell the fake GitHub about commits pushed to the PR branch."""
        state = self.read_state()
        prs = state["prs"]
        assert isinstance(prs, dict)
        prs["1"]["head_sha"] = git(repo, "rev-parse", "HEAD").strip()
        prs["1"]["files"] = [
            {"filename": "paper.qmd", "patch": patch(repo)},
        ]
        self.state.write_text(json.dumps(state), encoding="utf-8")


def patch(repo: Path) -> str:
    diff = git(repo, "diff", "main...HEAD", "--", "paper.qmd")
    return diff[diff.index("@@") :] if "@@" in diff else ""


def launcher(directory: Path, user: str, state: Path) -> Path:
    script = directory / f"gh-{user}"
    script.write_text(
        "#!/bin/sh\n"
        f'FAKE_GH_STATE="{state}" FAKE_GH_USER={user} exec "{sys.executable}" "{FAKE_GH}" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def make_env(base: Path) -> ReviewEnv:
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    alice_dir = base / "alice"
    builders.make_paper(alice_dir)
    git(alice_dir, "init", "-q", "-b", "main")
    git(alice_dir, "config", "user.name", "Alice")
    git(alice_dir, "config", "user.email", "alice@example.com")
    git(alice_dir, "add", "-A")
    git(alice_dir, "commit", "-q", "-m", "docs: first draft")
    git(alice_dir, "remote", "add", "origin", str(origin))
    git(alice_dir, "push", "-q", "-u", "origin", "main")
    git(alice_dir, "checkout", "-q", "-b", "edits")
    paper = alice_dir / "paper.qmd"
    paper.write_text(paper.read_text("utf-8").replace("fell by 4.5%", "fell by 4.8%"), "utf-8")
    git(alice_dir, "commit", "-q", "-am", "docs: update retail figure")
    git(alice_dir, "push", "-q", "-u", "origin", "edits")

    bob_dir = base / "bob"
    subprocess.run(["git", "clone", "-q", "-b", "edits", str(origin), str(bob_dir)], check=True)
    git(bob_dir, "config", "user.name", "Bob")
    git(bob_dir, "config", "user.email", "bob@example.com")

    state = base / "github.json"
    state.write_text(
        json.dumps(
            {
                "repo": "acme/deposit-review",
                "user": "alice",
                "next_id": 100,
                "comments": [],
                "threads": {},
                "prs": {
                    "1": {
                        "title": "Update retail figure",
                        "branch": "edits",
                        "author": "alice",
                        "head_sha": git(alice_dir, "rev-parse", "HEAD").strip(),
                        "files": [{"filename": "paper.qmd", "patch": patch(alice_dir)}],
                    },
                    "2": {
                        "title": "CI only",
                        "branch": "ci",
                        "author": "bob",
                        "head_sha": "0" * 40,
                        "files": [{"filename": ".github/workflows/build.yml", "patch": ""}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return ReviewEnv(
        state=state,
        alice_dir=alice_dir,
        bob_dir=bob_dir,
        alice=Gh(alice_dir, executable=str(launcher(base, "alice", state))),
        bob=Gh(bob_dir, executable=str(launcher(base, "bob", state))),
    )


def line_of(repo: Path, text: str) -> int:
    lines = (repo / "paper.qmd").read_text(encoding="utf-8").splitlines()
    return next(n for n, line in enumerate(lines, start=1) if text in line)
