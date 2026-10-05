"""`galley demo`: the whole process on sample material, with a simulated GitHub.

The demo writes a sample legacy whitepaper to convert. When its Review tab is
opened, the paper is turned into a Git repo with a pull request on a local
stand-in for GitHub (:mod:`galley.demo.fake_gh`), a teammate's comments are
seeded, and that teammate answers what you write. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from galley.demo import fake_gh, sample
from galley.gh import Gh, GhError
from galley.review.preview import prose_lines
from galley.review.session import ReviewSession

DEMO_DIR = ".galley-demo"
ORIGINALS = "originals"
YOU = "you"
TEAMMATE = "alice"
BRANCH = "review-edits"
ADDED_LINE = "This draft was reviewed for the October risk committee."
REPLIES = (
    "Thanks, that answers it.",
    "Agreed. I'll update the source table to match.",
    "Good catch. Can you push the edit so I can resolve this?",
)
BIBLIOGRAPHY = """\
@techreport{bcbs2013,
  author      = {{Basel Committee on Banking Supervision}},
  title       = {Basel III: The Liquidity Coverage Ratio and liquidity risk monitoring tools},
  institution = {Bank for International Settlements},
  year        = {2013}
}

@article{diamond1983,
  author  = {Diamond, Douglas W. and Dybvig, Philip H.},
  title   = {Bank Runs, Deposit Insurance, and Liquidity},
  journal = {Journal of Political Economy},
  volume  = {91},
  number  = {3},
  pages   = {401--419},
  year    = {1983}
}
"""


def prepare_workspace(workspace: Path) -> dict[str, str]:
    """Write the sample original and its bibliography; return their paths."""
    originals = workspace / ORIGINALS
    originals.mkdir(parents=True, exist_ok=True)
    document = originals / "legacy-whitepaper.docx"
    bibliography = originals / "references.bib"
    if not document.is_file():
        image = sample.make_png(workspace / DEMO_DIR / "chart.png")
        sample.make_docx(document, image, references=True, chart=True, extras=True)
    if not bibliography.is_file():
        bibliography.write_text(BIBLIOGRAPHY, encoding="utf-8")
    return {"sample": str(document), "bib": str(bibliography), "slug": "legacy-whitepaper"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Galley Demo", "-c", "user.email=demo@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise GhError(f"demo setup: git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def launcher(demo_dir: Path, slug: str, user: str) -> Path:
    """A `gh` stand-in for ``user``, bound to this paper's simulated GitHub."""
    script = demo_dir / f"gh-{slug}-{user}"
    state = demo_dir / f"github-{slug}.json"
    script.write_text(
        "#!/bin/sh\n"
        f'FAKE_GH_STATE="{state}" FAKE_GH_USER={user} '
        f'exec "{sys.executable}" "{Path(fake_gh.__file__).resolve()}" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _patch(repo: Path) -> str:
    diff = _git(repo, "diff", f"main...{BRANCH}", "--", "paper.qmd")
    return diff[diff.index("@@") :] if "@@" in diff else ""


def simulate_pull_request(paper_dir: Path, demo_dir: Path) -> str:
    """Give ``paper_dir`` a pull request on the simulated GitHub; return your `gh` stand-in.

    Safe to call again: an existing simulation is reused.
    """
    slug = paper_dir.name
    demo_dir.mkdir(parents=True, exist_ok=True)
    state = demo_dir / f"github-{slug}.json"
    mine = launcher(demo_dir, slug, YOU)
    theirs = launcher(demo_dir, slug, TEAMMATE)
    if state.is_file():
        return str(mine)

    origin = demo_dir / f"origin-{slug}.git"
    if not (paper_dir / ".git").exists():
        _git(paper_dir, "init", "-q", "-b", "main")
    _git(paper_dir, "config", "user.name", "Galley Demo")
    _git(paper_dir, "config", "user.email", "demo@example.invalid")
    _git(paper_dir, "checkout", "-q", "-B", "main")
    _git(paper_dir, "add", "-A")
    _git(paper_dir, "commit", "-q", "--allow-empty", "-m", "docs: convert legacy whitepaper")
    if not origin.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    if "origin" not in _git(paper_dir, "remote").split():
        _git(paper_dir, "remote", "add", "origin", str(origin))
    _git(paper_dir, "push", "-q", "-u", "origin", "main")
    _git(paper_dir, "checkout", "-q", "-B", BRANCH)
    paper = paper_dir / "paper.qmd"
    paper.write_text(paper.read_text(encoding="utf-8").rstrip() + f"\n\n{ADDED_LINE}\n", "utf-8")
    _git(paper_dir, "commit", "-q", "-am", "docs: note committee review")
    _git(paper_dir, "push", "-q", "-u", "origin", BRANCH)

    document: dict[str, Any] = {
        "repo": f"demo/{slug}",
        "user": YOU,
        "next_id": 100,
        "comments": [],
        "threads": {},
        "prs": {
            "1": {
                "title": "Note committee review",
                "branch": BRANCH,
                "author": TEAMMATE,
                "head_sha": _git(paper_dir, "rev-parse", "HEAD").strip(),
                "files": [{"filename": "paper.qmd", "patch": _patch(paper_dir)}],
            }
        },
    }
    state.write_text(json.dumps(document), encoding="utf-8")

    # A teammate has already left two comments: one outside the diff, one inside it.
    session = ReviewSession(paper_dir, Gh(paper_dir, executable=str(theirs)))
    session.start()
    session.select_pr(1)
    lines = paper.read_text(encoding="utf-8").splitlines()
    usable = prose_lines(lines)
    figure = next(
        (n for n, (line, ok) in enumerate(zip(lines, usable, strict=True), 1)
         if ok and "%" in line and ADDED_LINE not in line),
        None,
    )  # fmt: skip
    if figure is not None:
        session.add_comment(figure, "Can you confirm these figures against the **source table**?")
    added = next((n for n, line in enumerate(lines, 1) if ADDED_LINE in line), None)
    if added is not None:
        session.add_comment(added, "Which committee meeting is this for?")
    return str(mine)


class Teammate:
    """Answers your comments a few seconds later, so the sync is visible."""

    def __init__(self, paper_dir: Path, demo_dir: Path, *, interval: float = 4.0) -> None:
        slug = paper_dir.name
        self.state = demo_dir / f"github-{slug}.json"
        self.gh = Gh(paper_dir, executable=str(launcher(demo_dir, slug, TEAMMATE)))
        self.interval = interval
        self.answered: set[int] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="galley-demo-teammate", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def answer_once(self) -> int:
        """Reply to every thread whose latest comment is yours; return how many."""
        try:
            comments = json.loads(self.state.read_text(encoding="utf-8"))["comments"]
        except (OSError, json.JSONDecodeError, KeyError):
            return 0
        latest: dict[int, dict[str, Any]] = {}
        for comment in sorted(comments, key=lambda c: c["id"]):
            latest[int(comment["in_reply_to_id"] or comment["id"])] = comment
        replies = 0
        for root, comment in latest.items():
            if comment["user"]["login"] != YOU or int(comment["id"]) in self.answered:
                continue
            self.answered.add(int(comment["id"]))
            try:
                self.gh.reply(1, root, REPLIES[len(self.answered) % len(REPLIES)])
                replies += 1
            except GhError:
                continue
        return replies

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.answer_once()
