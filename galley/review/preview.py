"""The live preview: annotated temporary copies, rendered in the background.

The source files are never modified. Before each render Galley writes a copy
of every document with an anchor span at each commented line; under the
``review`` profile ``galley-review.lua`` turns the spans into margin notes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from galley import build, tools
from galley.config import load_paper_config
from galley.review.model import Thread
from galley.template import EXTENSION_DIR

PREVIEW_TAG = ".galley-preview"
DEBOUNCE_SECONDS = 1.5
_INCLUDE = re.compile(r"(\{\{<\s*include\s+)(\S+)(\s*>\}\})")
_NOT_PROSE = ("#|", ":::", "|", "![", "{{<", "---", "<!--", "$$", "```", "~~~", "#", "[^")
_FENCE = re.compile(r"^\s*(```|~~~)")
SEARCH_WINDOW = 15


def preview_name(path: str) -> str:
    """``sections/01-intro.qmd`` -> ``sections/01-intro.galley-preview.qmd``."""
    source = Path(path)
    return source.with_name(source.stem + PREVIEW_TAG + source.suffix).as_posix()


def prose_lines(lines: list[str]) -> list[bool]:
    """For each line, whether an anchor span can be appended to it safely."""
    usable: list[bool] = []
    in_front_matter = bool(lines) and lines[0].strip() == "---"
    in_code = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if in_front_matter:
            usable.append(False)
            if index > 0 and stripped in ("---", "..."):
                in_front_matter = False
            continue
        if _FENCE.match(line):
            in_code = not in_code
            usable.append(False)
            continue
        usable.append(
            not in_code
            and bool(stripped)
            and not stripped.startswith(_NOT_PROSE)
            and not stripped.endswith(("\\", "}", "|"))
        )
    return usable


def anchor_span(thread: Thread) -> str:
    state = "resolved" if thread.resolved else "open"
    initials = thread.as_dict()["initials"]
    return (
        f' []{{.galley-comment ref="{thread.ref}" n="{thread.number}" '
        f'initials="{initials}" state="{state}"}}'
    )


def annotate(text: str, threads: list[Thread]) -> str:
    """Return ``text`` with an anchor span at (or just after) every commented line."""
    lines = text.split("\n")
    usable = prose_lines(lines)
    additions: dict[int, list[str]] = {}
    for thread in sorted(threads, key=lambda t: t.number):
        if thread.line is None:
            continue
        start = min(max(thread.line - 1, 0), len(lines) - 1)
        after = range(start, min(start + SEARCH_WINDOW, len(lines)))
        before = range(start - 1, max(start - SEARCH_WINDOW, -1), -1)
        target = next((i for i in (*after, *before) if usable[i]), None)
        if target is not None:
            additions.setdefault(target, []).append(anchor_span(thread))
    for index, spans in additions.items():
        lines[index] = lines[index].rstrip() + "".join(spans)
    return "\n".join(lines)


def included_files(text: str) -> list[str]:
    return [found.group(2) for found in _INCLUDE.finditer(text)]


def stage(paper_dir: Path, document: str, texts: dict[str, str], threads: list[Thread]) -> Path:
    """Write annotated copies of ``document`` and the files it includes; return the main copy.

    ``texts`` holds unsaved editor contents by path; other files are read from disk.
    """

    def read(path: str) -> str | None:
        if path in texts:
            return texts[path]
        target = paper_dir / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    main_text = read(document)
    if main_text is None:
        raise FileNotFoundError(f"{document} not found in {paper_dir}")
    staged: dict[str, str] = {}
    for include in included_files(main_text):
        content = read(include)
        if content is not None and include.endswith(".qmd"):
            staged[include] = annotate(content, [t for t in threads if t.path == include])
    main_text = _INCLUDE.sub(
        lambda m: (
            m.group(1)
            + (preview_name(m.group(2)) if m.group(2) in staged else m.group(2))
            + m.group(3)
        ),
        main_text,
    )
    staged[document] = annotate(main_text, [t for t in threads if t.path == document])
    for path, content in staged.items():
        target = paper_dir / preview_name(path)
        target.write_text(content, encoding="utf-8")
    return paper_dir / preview_name(document)


def render_command(staged: Path, mode: str) -> list[str]:
    """The quarto command for a preview. ``mode`` is ``html`` (fast) or ``pdf`` (proof)."""
    quarto = shutil.which("quarto") or "quarto"
    command = [quarto, "render", staged.name, "--profile", "review", "-M", "galley-review:true"]
    if mode == "pdf":
        return [*command, "--to", "galley-pdf"]
    review_filter = EXTENSION_DIR / "filters" / "galley-review.lua"
    return [*command, "--to", "html", "--lua-filter", review_filter.as_posix()]


def output_name(document: str, mode: str) -> str:
    return Path(preview_name(document)).with_suffix(".pdf" if mode == "pdf" else ".html").name


@dataclass
class RenderStatus:
    state: str = "idle"  # idle | waiting | rendering | ok | error
    mode: str = "html"
    error: str = ""
    output: str = ""  # file name of the last successful render
    serial: int = 0  # bumps on every successful render

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


Source = Callable[[], tuple[str, dict[str, str], list[Thread]]]


class PreviewWorker:
    """Renders the preview off the request thread: debounced, newest request wins."""

    def __init__(
        self,
        paper_dir: Path,
        source: Source,
        *,
        debounce: float = DEBOUNCE_SECONDS,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.paper_dir = paper_dir
        self.source = source
        self.debounce = debounce
        self.on_change = on_change or (lambda: None)
        self.status = RenderStatus()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._due: float | None = None
        self._generation = 0
        self._process: subprocess.Popen[str] | None = None
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name="galley-preview", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True
        self._cancel_running()
        self._wake.set()

    def request(self, mode: str | None = None, *, immediate: bool = False) -> None:
        """Ask for a render ``debounce`` seconds from now, cancelling any stale one."""
        with self._lock:
            if mode is not None:
                self.status.mode = mode
            self._generation += 1
            self._due = time.monotonic() + (0 if immediate else self.debounce)
            if self.status.state not in ("rendering",):
                self.status.state = "waiting"
        self._cancel_running()
        self._wake.set()
        self.on_change()

    def _cancel_running(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def _loop(self) -> None:
        while not self._stopped:
            with self._lock:
                due = self._due
            if due is None:
                self._wake.wait()
                self._wake.clear()
                continue
            remaining = due - time.monotonic()
            if remaining > 0:
                self._wake.wait(remaining)
                self._wake.clear()
                continue
            with self._lock:
                generation = self._generation
                self._due = None
                self.status.state = "rendering"
                mode = self.status.mode
            self.on_change()
            self._render(generation, mode)

    def _run(self, command: list[str]) -> tuple[int, str]:
        config = load_paper_config(self.paper_dir)
        self._process = subprocess.Popen(
            command,
            cwd=self.paper_dir,
            env=tools.render_env(config.engine),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output, _ = self._process.communicate()
        return self._process.returncode, output

    def _render(self, generation: int, mode: str) -> None:
        try:
            document, texts, threads = self.source()
            staged = stage(self.paper_dir, document, texts, threads)
            code, output = self._run(render_command(staged, mode))
            error = "" if code == 0 else "\n".join(output.strip().splitlines()[-12:])
        except (OSError, FileNotFoundError) as exc:
            document, code, error = "", 1, str(exc)
        with self._lock:
            if generation != self._generation:
                return  # a newer request superseded this render; its result is stale
            if code == 0:
                self.status.state = "ok"
                self.status.error = ""
                self.status.output = output_name(document, mode)
                self.status.serial += 1
            else:
                # The previous preview stays on screen; only the status bar changes.
                self.status.state = "error"
                self.status.error = error or "render failed"
        self.on_change()


def main_document(paper_dir: Path) -> str:
    return build.main_document(paper_dir)
