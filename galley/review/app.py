"""The Dash app: a thin shell that carries state between the session and the browser.

The page itself (editor, inline threads, preview pane) is drawn by
``assets/galley-review.js`` from the state snapshot this module publishes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import Response, abort, send_from_directory

from galley.gh import Gh, GhError
from galley.review import preview as previews
from galley.review.git import GitError
from galley.review.preview import PREVIEW_TAG, PreviewWorker
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


def layout() -> html.Div:
    return html.Div(
        id="galley-app",
        children=[
            html.Div(id="gl-topbar"),
            html.Div(id="gl-banner"),
            html.Div(
                id="gl-panes",
                children=[
                    html.Div(
                        id="gl-left",
                        children=[html.Div(id="gl-loose"), html.Div(id="gl-editor")],
                    ),
                    html.Div(
                        id="gl-right",
                        children=[
                            html.Iframe(id="gl-preview", title="Preview"),
                            html.Div(id="gl-preview-empty"),
                        ],
                    ),
                ],
            ),
            html.Div(id="gl-toast"),
            dcc.Store(id="store-state"),
            dcc.Store(id="store-file"),
            dcc.Store(id="store-action"),
            dcc.Store(id="store-seen", data={"version": "", "epoch": -1}),
            dcc.Interval(id="tick", interval=1000),
        ],
    )


def create_app(controller: Controller, repo_dir: Path) -> Dash:
    app = Dash(
        __name__,
        title="Galley Review",
        update_title="",
        assets_folder=str(Path(__file__).resolve().parent / "assets"),
    )
    app.layout = layout()

    @app.callback(
        Output("store-state", "data"),
        Output("store-file", "data"),
        Input("tick", "n_intervals"),
        Input("store-action", "data"),
        State("store-seen", "data"),
    )
    def publish(_ticks: int | None, action: dict[str, Any] | None, seen: dict[str, Any]) -> Any:
        if ctx.triggered_id == "store-action" and action:
            controller.handle(action)
        state: Any = no_update
        file: Any = no_update
        if controller.version() != seen.get("version"):
            state = controller.snapshot()
        if controller.session.file_epoch != seen.get("epoch"):
            file = controller.file()
        return state, file

    app.clientside_callback(
        "function(state, file) { return window.galleyReview.update(state, file); }",
        Output("store-seen", "data"),
        Input("store-state", "data"),
        Input("store-file", "data"),
    )

    def preview_file(name: str) -> Response:
        # Only the temporary preview outputs are served, never the rest of the repo.
        if PREVIEW_TAG not in name.split("/")[0]:
            abort(404)
        response = send_from_directory(repo_dir, name)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.server.add_url_rule("/preview/<path:name>", view_func=preview_file)
    return app


def build(repo_dir: Path, gh: Gh, *, pr: int | None = None) -> tuple[Dash, Controller]:
    """Wire up a session, its poller and preview worker, and the app around them."""
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
    return create_app(controller, repo_dir), controller
