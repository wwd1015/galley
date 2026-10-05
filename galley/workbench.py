"""The Galley app: every step of a paper's life in one local window.

Papers, Convert, Build, Verify and Review are tabs over one workspace
directory. Long steps run as background jobs whose progress the page shows;
the Review tab hosts the pull-request review described in
:mod:`galley.review`.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import Response, abort, send_from_directory

from galley import build, doctor, manifest, scaffold
from galley import convert as converter
from galley import demo as demos
from galley import verify as verifier
from galley.config import ConfigError, load_paper_config
from galley.gh import Gh, GhError
from galley.review.app import Controller, build_controller
from galley.review.git import GitError
from galley.review.model import render_markdown
from galley.review.preview import PREVIEW_TAG
from galley.template import EXTENSION_DIR, PAPER_CONFIG
from galley.verify.report import ACCEPT_FILE

UPLOADS = ".galley-uploads"
SERVED_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".svg")
TABS = ("papers", "convert", "build", "verify", "review")
JobFunction = Callable[[build.Progress], dict[str, Any]]


def is_paper(directory: Path) -> bool:
    return (directory / EXTENSION_DIR / PAPER_CONFIG).is_file()


@dataclass
class Job:
    """One long-running step and how far it has got."""

    kind: str = ""
    title: str = ""
    state: str = "idle"  # idle | running | done | failed
    steps: list[str] = field(default_factory=list)
    error: str = ""
    summary: str = ""
    started: float = 0.0
    finished: float = 0.0
    paper: str = ""

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished or time.time()) - self.started if self.started else 0.0
        return {**self.__dict__, "elapsed": round(elapsed, 1)}


class Workbench:
    """State and actions for the whole app. One job runs at a time."""

    def __init__(
        self,
        workspace: Path,
        *,
        hostname: str | None = None,
        gh_executable: str = "gh",
        demo: bool = False,
    ) -> None:
        workspace = workspace.resolve()
        self.paper: str | None = None
        if is_paper(workspace):
            # Started inside a paper repo: its siblings are the workspace.
            self.paper = workspace.name
            workspace = workspace.parent
        self.workspace = workspace
        self.hostname = hostname
        self.gh_executable = gh_executable
        self.tab = "papers"
        self.job = Job()
        self.doctor: dict[str, Any] | None = None
        self.review: Controller | None = None
        self.review_paper: str | None = None
        self.review_error = ""
        self.upload: dict[str, str] = {}
        self.message: dict[str, Any] = {"text": "", "kind": "info", "serial": 0}
        self.changes = 0
        self.ack = ""
        # Demo mode: sample material to convert and a simulated GitHub for the Review tab.
        self.demo: dict[str, str] | None = demos.prepare_workspace(workspace) if demo else None
        self._teammate: demos.Teammate | None = None
        self._lock = threading.RLock()
        if self.paper is None:
            papers = self.paper_dirs()
            if len(papers) == 1:
                self.paper = papers[0].name

    # -- papers ------------------------------------------------------------

    def paper_dirs(self) -> list[Path]:
        if not self.workspace.is_dir():
            return []
        return sorted(d for d in self.workspace.iterdir() if d.is_dir() and is_paper(d))

    @property
    def paper_dir(self) -> Path | None:
        if self.paper is None:
            return None
        directory = self.workspace / self.paper
        return directory if is_paper(directory) else None

    def _require_paper(self) -> Path:
        directory = self.paper_dir
        if directory is None:
            raise ValueError("Select a paper first.")
        return directory

    @staticmethod
    def original(directory: Path) -> Path | None:
        return next(iter(sorted((directory / "source").glob("original.*"))), None)

    def _paper_summary(self, directory: Path) -> dict[str, Any]:
        document = build.main_document(directory)
        report = _read_json(directory / build.REPORT)
        verify = _read_json(directory / "verify-report.json")
        original = self.original(directory)
        return {
            "slug": directory.name,
            "document": document,
            "has_pdf": (directory / Path(document).with_suffix(".pdf").name).is_file(),
            "build_ok": report.get("ok") if report else None,
            "verify": verify.get("status") if verify else None,
            "source": original.name if original else None,
            "git": (directory / ".git").exists(),
        }

    # -- messages and jobs -------------------------------------------------

    def notify(self, text: str, kind: str = "info") -> None:
        with self._lock:
            self.message = {"text": text, "kind": kind, "serial": self.message["serial"] + 1}
            self.changes += 1

    def _progress(self, step: str) -> None:
        with self._lock:
            self.job.steps.append(step)
            self.changes += 1

    def start_job(
        self, kind: str, title: str, function: JobFunction, *, wait: bool = False
    ) -> None:
        """Run ``function`` in the background, recording each step it reports."""
        with self._lock:
            if self.job.state == "running":
                raise ValueError(f"Wait for the current step ({self.job.title}) to finish.")
            self.job = Job(kind=kind, title=title, state="running", started=time.time(),
                           paper=self.paper or "")  # fmt: skip
            self.changes += 1

        def run() -> None:
            try:
                outcome = function(self._progress)
                state, error, summary = "done", "", str(outcome.get("summary", ""))
                if outcome.get("failed"):
                    state = "failed"
            except Exception as exc:  # a job must never take the app down
                state, error, summary = "failed", str(exc), ""
            with self._lock:
                self.job.state, self.job.error, self.job.summary = state, error, summary
                self.job.finished = time.time()
                self.changes += 1

        thread = threading.Thread(target=run, name=f"galley-{kind}", daemon=True)
        thread.start()
        if wait:
            thread.join()

    # -- the steps ---------------------------------------------------------

    def run_doctor(self) -> None:
        try:
            config = load_paper_config(self.paper_dir)
        except ConfigError as exc:
            self.notify(str(exc), "error")
            return
        diagnosis = doctor.diagnose(config)
        with self._lock:
            self.doctor = {
                "versions": diagnosis.versions,
                "problems": diagnosis.problems,
                "ok": diagnosis.ok,
            }
            self.changes += 1

    def new_paper(self, slug: str) -> None:
        directory = scaffold.new_paper(slug, self.workspace)
        with self._lock:
            self.paper = directory.name
            self.changes += 1
        self.notify(f"Created {directory.name}. Build it next.")

    def convert(
        self, source: str, slug: str, bib: str, source_url: str, wait: bool = False
    ) -> None:
        if not scaffold.SLUG.match(slug):
            raise ValueError("The paper id must be lowercase letters, digits and hyphens.")
        if not source.strip():
            raise ValueError("Choose a file, or enter a path or a Google Docs URL.")
        out = self.workspace / slug
        bib_path = Path(bib).expanduser() if bib.strip() else None
        if bib_path is not None and not bib_path.is_file():
            raise ValueError(f"Bibliography file not found: {bib_path}")

        def job(progress: build.Progress) -> dict[str, Any]:
            url = source_url.strip()
            if converter.GOOGLE_DOC.match(source.strip()):
                progress("Exporting the Google Doc as .docx")
                url = url or source.strip()
                path = converter.export_google_doc(
                    source.strip(), self.workspace / UPLOADS / f"{slug}.docx"
                )
            else:
                path = Path(source.strip()).expanduser()
            conversion, report = converter.convert(
                path, out, bib=bib_path, source_url=url, progress=progress
            )
            with self._lock:
                self.paper = slug
            status = str(report["status"]).upper() if report else "not run"
            return {
                "summary": (
                    f"{len(conversion.tables)} table(s), {len(conversion.charts)} chart(s) "
                    f"rebuilt, {len(conversion.needs_data)} figure(s) needing data, "
                    f"{len(conversion.unmapped_styles)} unmapped style(s), "
                    f"{len(conversion.unresolved_citations)} unresolved citation(s). "
                    f"Verify: {status}."
                ),
            }

        self.start_job("convert", f"Convert to {slug}", job, wait=wait)

    def build_paper(self, wait: bool = False) -> None:
        directory = self._require_paper()

        def job(progress: build.Progress) -> dict[str, Any]:
            result = build.build(directory, progress=progress)
            if result.ok:
                pages = result.report["output"]["pages"]
                return {"summary": f"Built {directory.name}: {pages} page(s), all checks passed."}
            return {"failed": True, "summary": "\n".join(result.problems)}

        self.start_job("build", f"Build {directory.name}", job, wait=wait)

    def add_data(self, path: str) -> None:
        directory = self._require_paper()
        entry = manifest.add(directory, Path(path))
        self.notify(f"Recorded {entry.path} in the manifest.")

    def verify_paper(self, source: str = "", wait: bool = False) -> None:
        directory = self._require_paper()
        original = Path(source).expanduser() if source.strip() else self.original(directory)
        if original is None:
            raise ValueError(
                "This paper has no source/original.* file. Enter the path of the original "
                "document to compare against."
            )

        def job(progress: build.Progress) -> dict[str, Any]:
            report = verifier.verify(original, directory, progress=progress)
            failing = [c["title"] for c in report["checks"] if c["status"] == "fail"]
            if failing:
                return {"failed": True, "summary": "Failed: " + ", ".join(failing) + "."}
            return {"summary": "Every check passed."}

        self.start_job("verify", f"Verify {directory.name}", job, wait=wait)

    def accept(self, finding: str, reason: str, approved_by: str) -> None:
        """Record a human's acceptance of one verify finding, then re-check."""
        directory = self._require_paper()
        if not reason.strip():
            raise ValueError("Give a reason for accepting this difference.")
        path = directory / ACCEPT_FILE
        entries: list[Any] = []
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
            entries = loaded.get("accept", []) if isinstance(loaded, dict) else loaded
        entries = [e for e in entries if isinstance(e, dict) and e.get("id") != finding]
        entry = {"id": finding, "reason": reason.strip()}
        if approved_by.strip():
            entry["approved-by"] = approved_by.strip()
        entries.append(entry)
        path.write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")
        self.verify_paper()

    # -- review ------------------------------------------------------------

    def open_review(self, pr: int | None = None) -> None:
        """Start (or restart) the review session for the selected paper."""
        directory = self._require_paper()
        if self.review is not None and self.review_paper == directory.name and pr is None:
            return
        self.close_review()
        executable = self.gh_executable
        if self.demo is not None:
            try:
                demo_dir = self.workspace / demos.DEMO_DIR
                executable = demos.simulate_pull_request(directory, demo_dir)
                self._teammate = demos.Teammate(directory, demo_dir)
                self._teammate.start()
            except (GhError, GitError, OSError) as exc:
                with self._lock:
                    self.review_error = f"The simulated pull request could not be set up: {exc}"
                    self.changes += 1
                return
        if not (directory / ".git").exists():
            with self._lock:
                self.review_error = (
                    f"{directory.name} is not a Git repository yet. Run `git init` in it, push it "
                    "to GitHub and open a pull request; then come back to this tab."
                )
                self.changes += 1
            return
        try:
            gh = Gh(directory, self.hostname, executable)
            controller = build_controller(directory, gh, pr=pr)
        except (GhError, GitError, ConfigError) as exc:
            with self._lock:
                self.review_error = (
                    f"Review needs this paper to be a GitHub repository you can reach with "
                    f"`gh`: {exc}"
                )
                self.changes += 1
            return
        with self._lock:
            self.review, self.review_paper, self.review_error = controller, directory.name, ""
            self.changes += 1

    def close_review(self) -> None:
        with self._lock:
            if self._teammate is not None:
                self._teammate.stop()
                self._teammate = None
            if self.review is not None:
                self.review.stop()
            self.review, self.review_paper = None, None
            self.changes += 1

    # -- actions -----------------------------------------------------------

    def handle(self, action: dict[str, Any]) -> None:
        """Carry out one action from the page; failures become a message, never a crash."""
        kind = str(action.get("type", ""))
        if kind == "batch":
            # The page sends its actions in order, one batch at a time, and waits for the
            # acknowledgement before sending more, so none can overtake or replace another.
            for item in action.get("actions") or []:
                if isinstance(item, dict):
                    self.handle(item)
            with self._lock:
                self.ack = str(action.get("nonce", ""))
                self.changes += 1
            return
        if not kind.startswith("wb_"):
            if self.review is not None:
                self.review.handle(action)
            return
        try:
            if kind == "wb_tab":
                tab = str(action.get("tab"))
                if tab in TABS:
                    self.tab = tab
                    self.changes += 1
                    if tab == "review" and self.paper_dir is not None:
                        self.open_review()
            elif kind == "wb_select_paper":
                slug = str(action["slug"])
                if not is_paper(self.workspace / slug):
                    raise ValueError(f"{slug} is not a Galley paper.")
                with self._lock:
                    self.paper = slug
                    self.changes += 1
                if self.tab == "review":
                    self.open_review()
            elif kind == "wb_new_paper":
                self.new_paper(str(action["slug"]).strip())
            elif kind == "wb_doctor":
                self.run_doctor()
            elif kind == "wb_convert":
                self.convert(
                    str(action.get("source", "")),
                    str(action.get("slug", "")).strip(),
                    str(action.get("bib", "")),
                    str(action.get("source_url", "")),
                )
            elif kind == "wb_build":
                self.build_paper()
            elif kind == "wb_data_add":
                self.add_data(str(action["path"]))
            elif kind == "wb_verify":
                self.verify_paper(str(action.get("source", "")))
            elif kind == "wb_accept":
                self.accept(
                    str(action["id"]),
                    str(action.get("reason", "")),
                    str(action.get("approved_by", "")),
                )
            elif kind == "wb_open_review":
                pr = action.get("pr")
                self.open_review(int(pr) if pr else None)
        except (
            ValueError,
            KeyError,
            scaffold.ScaffoldError,
            converter.ConvertError,
            manifest.ManifestError,
            ConfigError,
        ) as exc:
            self.notify(str(exc.args[0]) if exc.args else str(exc), "error")

    def save_upload(self, name: str, contents: str) -> dict[str, str]:
        """Store a file dropped on the page; return its name and local path."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-") or "upload"
        _, _, encoded = contents.partition(",")
        target = self.workspace / UPLOADS / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
        with self._lock:
            self.upload = {"name": safe, "path": str(target)}
            self.changes += 1
        return self.upload

    # -- view --------------------------------------------------------------

    def version(self) -> str:
        review = self.review.version() if self.review is not None else "-"
        running = int(time.time()) if self.job.state == "running" else 0
        return f"{self.changes}.{review}.{running}"

    def _paper_detail(self, directory: Path) -> dict[str, Any]:
        document = build.main_document(directory)
        pdf = directory / Path(document).with_suffix(".pdf").name
        report_md = directory / converter.REPORT
        try:
            data_problems = manifest.verify(directory)
            entries = [e.__dict__ for e in manifest.load(directory)]
        except manifest.ManifestError as exc:
            data_problems, entries = [str(exc)], []
        return {
            "slug": directory.name,
            "path": str(directory),
            "document": document,
            "pdf": (
                f"/paper/{directory.name}/{pdf.name}?v={int(pdf.stat().st_mtime)}"
                if pdf.is_file()
                else ""
            ),
            "build": _read_json(directory / build.REPORT),
            "verify": _read_json(directory / "verify-report.json"),
            "conversion_html": (
                render_markdown(report_md.read_text(encoding="utf-8"))
                if report_md.is_file()
                else ""
            ),
            "source": (lambda o: o.name if o else None)(self.original(directory)),
            "data": {"entries": entries, "problems": data_problems},
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            directory = self.paper_dir
            return {
                "version": self.version(),
                "ack": self.ack,
                "demo": self.demo,
                "workspace": str(self.workspace),
                "tab": self.tab,
                "papers": [self._paper_summary(d) for d in self.paper_dirs()],
                "paper": directory.name if directory else None,
                "detail": self._paper_detail(directory) if directory else None,
                "job": self.job.as_dict(),
                "doctor": self.doctor,
                "upload": self.upload,
                "message": self.message,
                "review": self.review.snapshot() if self.review is not None else None,
                "review_error": self.review_error,
            }

    def file(self) -> dict[str, Any]:
        if self.review is None:
            return {"path": None, "epoch": -1, "text": ""}
        return self.review.file()

    def file_epoch(self) -> int:
        return self.review.session.file_epoch if self.review is not None else -1


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def layout() -> html.Div:
    return html.Div(
        id="galley-app",
        children=[
            html.Div(id="wb-nav"),
            html.Div(id="wb-job"),
            html.Div(
                id="wb-main",
                children=[
                    html.Div(id="wb-tab-papers", className="wb-tab"),
                    html.Div(
                        id="wb-tab-convert",
                        className="wb-tab",
                        children=[
                            html.Div(id="wb-convert-intro"),
                            dcc.Upload(
                                id="wb-upload",
                                className="wb-drop",
                                children=html.Div(
                                    "Drop a .docx or .pdf here, or click to choose one"
                                ),
                                accept=".docx,.pdf,.bib",
                                multiple=False,
                            ),
                            html.Div(id="wb-convert-body"),
                        ],
                    ),
                    html.Div(id="wb-tab-build", className="wb-tab"),
                    html.Div(id="wb-tab-verify", className="wb-tab"),
                    html.Div(
                        id="wb-tab-review",
                        className="wb-tab",
                        children=[
                            html.Div(id="wb-review-empty"),
                            html.Div(id="gl-topbar"),
                            html.Div(id="gl-banner"),
                            html.Div(
                                id="gl-panes",
                                children=[
                                    html.Div(
                                        id="gl-left",
                                        children=[
                                            html.Div(id="gl-loose"),
                                            html.Div(id="gl-editor"),
                                        ],
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
                        ],
                    ),
                ],
            ),
            html.Div(id="gl-toast"),
            dcc.Store(id="store-state"),
            dcc.Store(id="store-file"),
            dcc.Store(id="store-action"),
            dcc.Store(id="store-upload"),
            dcc.Store(id="store-seen", data={"version": "", "epoch": -2}),
            dcc.Interval(id="tick", interval=1000),
        ],
    )


def create_app(workbench: Workbench) -> Dash:
    app = Dash(
        __name__,
        title="Galley",
        update_title="",
        assets_folder=str(Path(__file__).resolve().parent / "review" / "assets"),
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
            workbench.handle(action)
        state: Any = no_update
        file: Any = no_update
        if workbench.version() != seen.get("version"):
            state = workbench.snapshot()
        if workbench.file_epoch() != seen.get("epoch"):
            file = workbench.file()
        return state, file

    @app.callback(
        Output("store-upload", "data"),
        Input("wb-upload", "contents"),
        State("wb-upload", "filename"),
        prevent_initial_call=True,
    )
    def uploaded(contents: str | None, filename: str | None) -> Any:
        if not contents or not filename:
            return no_update
        return workbench.save_upload(filename, contents)

    app.clientside_callback(
        "function(state, file) { return window.galleyApp.update(state, file); }",
        Output("store-seen", "data"),
        Input("store-state", "data"),
        Input("store-file", "data"),
    )

    def preview_file(name: str) -> Response:
        # Only the review's temporary preview outputs are served from here.
        directory = workbench.paper_dir
        if directory is None or PREVIEW_TAG not in name.split("/")[0]:
            abort(404)
        response = send_from_directory(directory, name)
        response.headers["Cache-Control"] = "no-store"
        return response

    def paper_file(slug: str, name: str) -> Response:
        # Built PDFs and figure images of papers in the workspace, nothing else.
        directory = workbench.workspace / slug
        if "/" in slug or not is_paper(directory) or not name.lower().endswith(SERVED_SUFFIXES):
            abort(404)
        response = send_from_directory(directory, name)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.server.add_url_rule("/preview/<path:name>", view_func=preview_file)
    app.server.add_url_rule("/paper/<slug>/<path:name>", view_func=paper_file)
    return app
