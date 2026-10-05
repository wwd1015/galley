"""The review controller: applies browser actions to a session and publishes its state.

The review page (editor, inline threads, preview pane) is drawn by
``assets/galley-review.js`` from the snapshot this module publishes. The Dash
app that hosts it is in :mod:`galley.workbench`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from galley.gh import Gh, GhError
from galley.review import preview as previews
from galley.review.git import GitError
from galley.review.preview import PreviewWorker
from galley.review.session import ReviewError, ReviewSession
from galley.review.sync import Poller

COMMENT_ACTIONS = {"comment", "reply", "edit_comment", "delete_comment", "resolve"}


class Controller:
    """Applies browser actions to the session and publishes snapshots back."""

    def __init__(self, session: ReviewSession, preview: PreviewWorker, poller: Poller) -> None:
        self.session = session
        self.preview = preview
        self.poller = poller
        self.render_changes = 0
        self._lock = threading.Lock()

    def stop(self) -> None:
        self.preview.stop()
        self.poller.stop()

    def render_changed(self) -> None:
        self.render_changes += 1

    def version(self) -> str:
        return f"{self.session.version}.{self.render_changes}"

    def snapshot(self) -> dict[str, Any]:
        state = self.session.snapshot()
        status = self.preview.status.as_dict()
        status["url"] = f"/preview/{status['output']}" if status["output"] else ""
        state["render"] = status
        state["version"] = self.version()
        return state

    def file(self) -> dict[str, Any]:
        session = self.session
        path = session.path
        return {
            "path": path,
            "epoch": session.file_epoch,
            "text": session.text(path) if path else "",
        }

    def handle(self, action: dict[str, Any]) -> None:
        """Carry out one action from the browser; failures become a message, never a crash."""
        session = self.session
        kind = str(action.get("type", ""))
        try:
            with self._lock:
                if kind == "select_pr":
                    session.select_pr(int(action["number"]))
                    self.preview.request(immediate=True)
                elif kind == "open_pr":
                    session.open_pr()
                    self.preview.request(immediate=True)
                elif kind == "checkout":
                    session.checkout()
                    self.preview.request(immediate=True)
                elif kind == "select_file":
                    session.select_file(str(action["path"]))
                elif kind == "edit":
                    session.edit(str(action["path"]), str(action["text"]))
                    self.preview.request()
                elif kind == "comment":
                    session.add_comment(int(action["line"]), str(action["body"]))
                elif kind == "reply":
                    session.reply(int(action["thread"]), str(action["body"]))
                elif kind == "edit_comment":
                    session.edit_comment(int(action["id"]), str(action["body"]))
                elif kind == "delete_comment":
                    session.delete_comment(int(action["id"]))
                elif kind == "resolve":
                    session.set_resolved(int(action["thread"]), bool(action["resolved"]))
                elif kind == "submit_review":
                    session.submit_review(str(action["event"]), str(action.get("body", "")))
                elif kind == "commit":
                    session.commit_and_push()
                elif kind == "pull":
                    session.pull()
                    self.preview.request(immediate=True)
                elif kind == "preview_mode":
                    mode = "pdf" if action.get("mode") == "pdf" else "html"
                    self.preview.request(mode, immediate=True)
                elif kind == "activity":
                    self.poller.set_idle(bool(action.get("idle")))
                elif kind == "seen":
                    session.mark_seen()
                elif kind == "refresh":
                    self.poller.poke()
                if kind in COMMENT_ACTIONS:
                    self.preview.request(immediate=True)
        except (ReviewError, GhError, GitError) as exc:
            session.notify(str(exc), "error")
        except (KeyError, ValueError, TypeError) as exc:
            session.notify(f"Malformed request from the page: {exc}", "error")


def build_controller(repo_dir: Path, gh: Gh, *, pr: int | None = None) -> Controller:
    """Wire up a review session for one paper repo, with its poller and preview worker."""
    repo_dir = repo_dir.resolve()
    session = ReviewSession(repo_dir, gh)
    session.start()

    def source() -> tuple[str, dict[str, str], list[Any]]:
        return previews.main_document(repo_dir), {}, list(session.threads)

    preview = PreviewWorker(repo_dir, source)

    def refresh() -> None:
        if session.refresh():
            preview.request(immediate=True)

    poller = Poller(refresh)
    controller = Controller(session, preview, poller)
    preview.on_change = controller.render_changed
    if pr is not None:
        session.select_pr(pr)
    elif len(session.prs) == 1:
        session.select_pr(int(session.prs[0]["number"]))
    preview.start()
    poller.start()
    if session.pr is not None:
        preview.request(immediate=True)
    return controller
