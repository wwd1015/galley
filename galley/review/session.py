"""One review session: a pull request, its ``.qmd`` files and their comment threads."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from galley.gh import Gh, GhError
from galley.review import anchors, git
from galley.review.model import Thread, build_threads, locate_threads

SUFFIX = ".qmd"


class ReviewError(Exception):
    """An action could not be carried out; the message is shown to the user."""


class ReviewSession:
    """All state the app shows. GitHub is the only shared state; this is a local view of it."""

    def __init__(self, repo_dir: Path, gh: Gh) -> None:
        self.repo_dir = repo_dir.resolve()
        self.gh = gh
        self.lock = threading.RLock()
        self.version = 0
        self.viewer = ""
        self.repo = ""
        self.prs: list[dict[str, Any]] = []
        self.pr: dict[str, Any] | None = None
        self.files: list[str] = []
        self.diff: dict[str, set[int]] = {}
        self.path: str | None = None
        self.file_epoch = 0
        self.threads: list[Thread] = []
        self.unsaved: dict[str, str] = {}  # editor text newer than the disk, by path
        self.last_poll: float | None = None
        self.new_comments = 0
        self.error = ""
        self.head_changed = False
        self.message: dict[str, Any] = {"text": "", "kind": "info", "serial": 0}
        self._comments_etag = ""
        self._pr_etag = ""
        self._raw_comments: list[dict[str, Any]] = []
        self._resolution: dict[int, dict[str, Any]] = {}
        self._seen: set[int] = set()

    # -- helpers -----------------------------------------------------------

    def _bump(self) -> None:
        self.version += 1

    def notify(self, text: str, kind: str = "info") -> None:
        with self.lock:
            self.message = {"text": text, "kind": kind, "serial": self.message["serial"] + 1}
            self._bump()

    def text(self, path: str) -> str:
        """Current text of ``path``: what is in the editor, else what is on disk."""
        if path in self.unsaved:
            return self.unsaved[path]
        target = self.repo_dir / path
        return target.read_text(encoding="utf-8") if target.is_file() else ""

    def _texts(self) -> dict[str, str]:
        return {path: self.text(path) for path in self.files}

    def _relocate(self) -> None:
        before = [(t.id, t.line, t.number) for t in self.threads]
        locate_threads(self.threads, self._texts())
        if before != [(t.id, t.line, t.number) for t in self.threads]:
            self._bump()

    def _number(self) -> int:
        if self.pr is None:
            raise ReviewError("Select a pull request first.")
        return int(self.pr["number"])

    # -- session -----------------------------------------------------------

    def start(self) -> None:
        """Identify the user and repository and list the pull requests to review."""
        viewer, repo, prs = self.gh.viewer(), self.gh.repo(), self.gh.list_prs(SUFFIX)
        with self.lock:
            self.viewer, self.repo, self.prs = viewer, repo, prs
            self._bump()

    def select_pr(self, number: int) -> None:
        response = self.gh.pr(number)
        files = self.gh.pr_files(number)
        info = response.body
        with self.lock:
            self.pr = {
                "number": number,
                "title": str(info["title"]),
                "branch": str(info["head"]["ref"]),
                "head_sha": str(info["head"]["sha"]),
                "url": str(info.get("html_url", "")),
            }
            self._pr_etag = response.etag
            self.files = [f["filename"] for f in files if f["filename"].endswith(SUFFIX)]
            self.diff = {
                f["filename"]: anchors.diff_lines(str(f.get("patch") or "")) for f in files
            }
            self.path = self.files[0] if self.files else None
            self.file_epoch += 1
            self.unsaved.clear()
            self.threads = []
            self._comments_etag = ""
            self._seen.clear()
            self.new_comments = 0
            self._bump()
        self.refresh(force=True, first=True)

    def open_pr(self) -> int:
        """Open a pull request from the current branch and start reviewing it."""
        branch = git.current_branch(self.repo_dir)
        number = self.gh.create_pr(f"Review: {branch}", "Opened from Galley Review.")
        prs = self.gh.list_prs(SUFFIX)
        with self.lock:
            self.prs = prs
        self.select_pr(number)
        return number

    def checkout(self) -> None:
        number = self._number()
        if git.is_dirty(self.repo_dir):
            raise ReviewError("Commit or stash your local changes before switching branches.")
        self.gh.checkout(number)
        with self.lock:
            self.file_epoch += 1
            self.unsaved.clear()
            self._relocate()
            self._bump()

    def select_file(self, path: str) -> None:
        with self.lock:
            if path not in self.files:
                raise ReviewError(f"{path} is not part of this pull request.")
            self.path = path
            self.file_epoch += 1
            self._bump()

    # -- sync --------------------------------------------------------------

    def refresh(self, *, force: bool = False, first: bool = False) -> bool:
        """Poll GitHub for comments, resolved state and the PR head. True if anything changed."""
        with self.lock:
            if self.pr is None:
                return False
            number = int(self.pr["number"])
            comments_etag = "" if force else self._comments_etag
            pr_etag = "" if force else self._pr_etag
        try:
            page = self.gh.comments_changed(number, comments_etag)
            raw: list[dict[str, Any]] | None = None
            if not page.not_modified:
                # One page holds everything unless the PR has a very long discussion.
                raw = page.body if len(page.body) < 100 else self.gh.comments(number)
            resolution = self.gh.review_threads(number)
            head = self.gh.pr(number, pr_etag)
        except GhError as exc:
            with self.lock:
                changed = self.error != str(exc)
                self.error = str(exc)
                self.last_poll = time.time()
                if changed:
                    self._bump()
            return changed
        with self.lock:
            changed = bool(self.error)
            self.error = ""
            self.last_poll = time.time()
            if raw is not None:
                self._comments_etag = page.etag
                changed = changed or raw != self._raw_comments
                self._raw_comments = raw
            if resolution != self._resolution:
                self._resolution = resolution
                changed = True
            if not head.not_modified:
                self._pr_etag = head.etag
                sha = str(head.body["head"]["sha"])
                if self.pr is not None and sha != self.pr["head_sha"]:
                    self.pr["head_sha"] = sha
                    changed = True
            behind = self._behind_remote()
            if behind != self.head_changed:
                self.head_changed = behind
                changed = True
            if changed or first:
                self.threads = build_threads(self._raw_comments, self._resolution, self.viewer)
                locate_threads(self.threads, self._texts())
                ids = {int(c["id"]) for c in self._raw_comments}
                theirs = {
                    int(c["id"]) for c in self._raw_comments
                    if (c.get("user") or {}).get("login") != self.viewer
                }  # fmt: skip
                if not first:
                    self.new_comments += len(theirs - self._seen)
                self._seen = ids
                self._bump()
            return changed

    def _behind_remote(self) -> bool:
        """True when the PR head on GitHub has commits this checkout does not."""
        if self.pr is None:
            return False
        remote = str(self.pr["head_sha"])
        try:
            if git.head_sha(self.repo_dir) == remote:
                return False
            git.run(self.repo_dir, "merge-base", "--is-ancestor", remote, "HEAD")
        except git.GitError:
            return True
        return False

    def mark_seen(self) -> None:
        with self.lock:
            if self.new_comments:
                self.new_comments = 0
                self._bump()

    # -- comments ----------------------------------------------------------

    def _thread(self, thread_id: int) -> Thread:
        for thread in self.threads:
            if thread.id == thread_id:
                return thread
        raise ReviewError("That comment thread no longer exists.")

    def can_comment_natively(self, path: str, line: int) -> bool:
        """GitHub accepts a line comment only inside the diff, at the PR head's line numbers."""
        if self.pr is None or line not in self.diff.get(path, set()):
            return False
        return git.file_at(self.repo_dir, str(self.pr["head_sha"]), path) == self.text(path)

    def add_comment(self, line: int, body: str) -> None:
        number = self._number()
        path = self.path
        if path is None:
            raise ReviewError("Select a file first.")
        if not body.strip():
            raise ReviewError("Write a comment first.")
        lines = self.text(path).splitlines()
        if not 1 <= line <= len(lines):
            raise ReviewError(f"Line {line} is not in {path}.")
        commit = str(self.pr["head_sha"]) if self.pr else ""
        posted = False
        if self.can_comment_natively(path, line):
            try:
                self.gh.create_line_comment(number, commit, path, line, body)
                posted = True
            except GhError:
                posted = False  # GitHub's view of the diff differs; fall back to an anchor
        if not posted:
            anchored = anchors.with_anchor(body, line, lines[line - 1])
            self.gh.create_file_comment(number, commit, path, anchored)
        self.refresh(force=True)

    def reply(self, thread_id: int, body: str) -> None:
        if not body.strip():
            raise ReviewError("Write a reply first.")
        self.gh.reply(self._number(), self._thread(thread_id).id, body)
        self.refresh(force=True)

    def edit_comment(self, comment_id: int, body: str) -> None:
        thread = next(
            (t for t in self.threads if any(c.id == comment_id for c in t.comments)), None
        )
        if thread is None:
            raise ReviewError("That comment no longer exists.")
        comment = next(c for c in thread.comments if c.id == comment_id)
        if not comment.mine:
            raise ReviewError("You can only edit your own comments.")
        if thread.kind == "anchored" and comment.id == thread.id:
            # Keep the hidden anchor, pointing at where the thread sits now.
            line = thread.line or thread.recorded_line or 1
            body = f"{anchors.make_anchor(line, thread.quote)}\n{body}"
        self.gh.edit_comment(comment_id, body)
        self.refresh(force=True)

    def delete_comment(self, comment_id: int) -> None:
        thread = next(
            (t for t in self.threads if any(c.id == comment_id for c in t.comments)), None
        )
        if thread is None:
            raise ReviewError("That comment no longer exists.")
        if not next(c for c in thread.comments if c.id == comment_id).mine:
            raise ReviewError("You can only delete your own comments.")
        self.gh.delete_comment(comment_id)
        self.refresh(force=True)

    def set_resolved(self, thread_id: int, resolved: bool) -> None:
        thread = self._thread(thread_id)
        if not thread.node_id:
            raise ReviewError("GitHub has not reported this thread yet; try again in a moment.")
        self.gh.set_resolved(thread.node_id, resolved)
        self.refresh(force=True)

    def submit_review(self, event: str, body: str) -> None:
        self.gh.submit_review(self._number(), event, body)
        labels = {"COMMENT": "Comment", "APPROVE": "Approval", "REQUEST_CHANGES": "Change request"}
        self.notify(f"{labels.get(event, event)} submitted.")

    # -- editing -----------------------------------------------------------

    def edit(self, path: str, text: str) -> None:
        """Save the editor's text to disk and re-place the threads in it."""
        with self.lock:
            if path not in self.files:
                raise ReviewError(f"{path} is not part of this pull request.")
            target = self.repo_dir / path
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                target.write_text(text, encoding="utf-8")
            self.unsaved.pop(path, None)
            self._relocate()

    def dirty(self) -> bool:
        try:
            return any(git.is_dirty(self.repo_dir, path) for path in self.files)
        except git.GitError:
            return False

    def commit_and_push(self) -> None:
        number = self._number()
        changed = [p for p in self.files if git.is_dirty(self.repo_dir, p)]
        if not changed:
            self.notify("Nothing to commit.")
            return
        names = ", ".join(Path(p).name for p in changed)
        message = f"review: edit {names} (PR #{number})"
        self.notify(git.commit_and_push(self.repo_dir, changed, message))
        self.refresh(force=True)

    def pull(self) -> None:
        self.notify(git.pull(self.repo_dir))
        with self.lock:
            self.file_epoch += 1
            self.unsaved.clear()
        self.refresh(force=True)

    # -- view --------------------------------------------------------------

    def verify_status(self) -> str | None:
        report = self.repo_dir / "verify-report.json"
        if not report.is_file():
            return None
        try:
            return str(json.loads(report.read_text(encoding="utf-8")).get("status"))
        except (OSError, json.JSONDecodeError):
            return None

    def branch(self) -> str:
        try:
            return git.current_branch(self.repo_dir)
        except git.GitError:
            return ""

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            branch = self.branch()
            visible = [t for t in self.threads if t.path == self.path]
            return {
                "version": self.version,
                "repo": self.repo,
                "viewer": self.viewer,
                "prs": self.prs,
                "pr": self.pr,
                "branch": branch,
                "on_pr_branch": self.pr is not None and branch == self.pr["branch"],
                "files": self.files,
                "path": self.path,
                "file_epoch": self.file_epoch,
                "threads": [t.as_dict() for t in sorted(visible, key=lambda t: t.number)],
                "open_threads": sum(1 for t in self.threads if not t.resolved),
                "sync": {
                    "last_poll": self.last_poll,
                    "new_comments": self.new_comments,
                    "error": self.error,
                    "head_changed": self.head_changed,
                },
                "dirty": self.dirty(),
                "verify": self.verify_status(),
                "message": self.message,
            }
