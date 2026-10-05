"""The app shell: every step driven the way the page drives it, through actions."""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from galley.workbench import Workbench, create_app, is_paper

from . import builders
from .review_env import ReviewEnv, make_env


def message(bench: Workbench) -> tuple[str, str]:
    return bench.message["kind"], bench.message["text"]


def test_empty_workspace_and_new_paper(tmp_path: Path) -> None:
    bench = Workbench(tmp_path)
    state = bench.snapshot()
    assert (state["papers"], state["paper"], state["detail"], state["tab"]) == (
        [],
        None,
        None,
        "papers",
    )
    assert state["job"]["state"] == "idle" and state["review"] is None

    before = bench.version()
    bench.handle({"type": "wb_new_paper", "slug": "liquidity-2026"})
    assert bench.version() != before
    state = bench.snapshot()
    assert state["paper"] == "liquidity-2026" and is_paper(tmp_path / "liquidity-2026")
    (summary,) = state["papers"]
    assert summary == {
        "slug": "liquidity-2026", "document": "paper.qmd", "has_pdf": False, "build_ok": None,
        "verify": None, "source": None, "git": True,
    }  # fmt: skip
    detail = state["detail"]
    assert detail["pdf"] == "" and detail["build"] is None and detail["verify"] is None
    assert detail["data"]["problems"] == []
    assert [e["path"] for e in detail["data"]["entries"]] == ["data/example.csv"]


def test_starting_inside_a_paper_uses_its_parent_as_workspace(tmp_path: Path) -> None:
    paper = builders.make_paper(tmp_path / "deposit-review")
    bench = Workbench(paper)
    assert bench.workspace == tmp_path.resolve() and bench.paper == "deposit-review"
    assert Workbench(tmp_path).paper == "deposit-review"  # the only paper is preselected


def test_bad_requests_become_messages(tmp_path: Path) -> None:
    bench = Workbench(tmp_path)
    bench.handle({"type": "wb_new_paper", "slug": "Bad Slug"})
    assert message(bench) == ("error", "slug must be lowercase letters, digits and hyphens")
    bench.handle({"type": "wb_build"})
    assert message(bench) == ("error", "Select a paper first.")
    bench.handle({"type": "wb_select_paper", "slug": "nope"})
    assert message(bench) == ("error", "nope is not a Galley paper.")
    bench.handle({"type": "wb_convert", "source": "", "slug": "x"})
    assert message(bench)[1].startswith("Choose a file")
    bench.handle(
        {"type": "wb_convert", "source": "/tmp/a.docx", "slug": "x", "bib": "/no/refs.bib"}
    )
    assert message(bench)[1] == "Bibliography file not found: /no/refs.bib"
    bench.handle({"type": "wb_tab", "tab": "verify"})
    assert bench.snapshot()["tab"] == "verify"
    bench.handle({"type": "wb_tab", "tab": "nonsense"})
    assert bench.tab == "verify"
    bench.handle({"type": "reply", "thread": 1, "body": "x"})  # review action with no review open


def test_batches_run_in_order_and_are_acknowledged(tmp_path: Path) -> None:
    bench = Workbench(tmp_path)
    bench.handle({
        "type": "batch", "nonce": "n-1",
        "actions": [{"type": "wb_new_paper", "slug": "paper-a"}, {"type": "wb_tab", "tab": "build"},
                    {"type": "wb_new_paper", "slug": "paper-b"}],
    })  # fmt: skip
    state = bench.snapshot()
    assert (state["ack"], state["tab"], state["paper"]) == ("n-1", "build", "paper-b")
    assert [p["slug"] for p in state["papers"]] == ["paper-a", "paper-b"]


def test_review_needs_a_git_repository(tmp_path: Path) -> None:
    builders.make_paper(tmp_path / "deposit-review")
    bench = Workbench(tmp_path)
    bench.handle({"type": "wb_tab", "tab": "review"})
    assert "is not a Git repository yet" in bench.snapshot()["review_error"]


def test_upload_is_saved_under_a_safe_name(tmp_path: Path) -> None:
    bench = Workbench(tmp_path)
    contents = "data:application/octet-stream;base64," + base64.b64encode(b"PK-bytes").decode()
    saved = bench.save_upload("../../My Paper (final).docx", contents)
    assert saved["name"] == "My-Paper-final-.docx"
    target = Path(saved["path"])
    assert (
        target.parent == tmp_path.resolve() / ".galley-uploads"
        and target.read_bytes() == b"PK-bytes"
    )
    assert bench.snapshot()["upload"] == saved


def test_one_job_at_a_time_and_failures_are_reported(tmp_path: Path) -> None:
    import threading

    bench = Workbench(tmp_path)
    gate = threading.Event()

    def slow(progress: object) -> dict[str, object]:
        assert callable(progress)
        progress("Step one")
        gate.wait(5)
        return {"summary": "finished"}

    bench.start_job("demo", "Demo", slow)
    with pytest.raises(ValueError, match="Wait for the current step"):
        bench.start_job("demo", "Again", slow)
    assert bench.snapshot()["job"]["state"] == "running"
    gate.set()
    for _ in range(200):
        if bench.job.state != "running":
            break
        threading.Event().wait(0.01)
    job = bench.snapshot()["job"]
    assert (job["state"], job["steps"], job["summary"]) == ("done", ["Step one"], "finished")

    def broken(_progress: object) -> dict[str, object]:
        raise RuntimeError("quarto exploded")

    bench.start_job("demo", "Broken", broken, wait=True)
    assert (bench.job.state, bench.job.error) == ("failed", "quarto exploded")


def test_routes_serve_the_page_and_only_outputs(tmp_path: Path) -> None:
    paper = builders.make_paper(tmp_path / "deposit-review")
    (paper / "paper.pdf").write_bytes(b"%PDF-1.4")
    (paper / "paper.galley-preview.html").write_text("<p>preview</p>", encoding="utf-8")
    bench = Workbench(tmp_path)
    client = create_app(bench).server.test_client()
    assert client.get("/").status_code == 200
    layout = str(client.get("/_dash-layout").get_json())
    for identifier in ("wb-nav", "wb-upload", "wb-tab-verify", "gl-editor", "store-action"):
        assert identifier in layout
    assert client.get("/paper/deposit-review/paper.pdf").status_code == 200
    assert client.get("/paper/deposit-review/figures/source/image1.png").status_code == 200
    assert client.get("/paper/deposit-review/paper.qmd").status_code == 404
    assert client.get("/paper/deposit-review/.git/config").status_code == 404
    assert client.get("/paper/not-a-paper/paper.pdf").status_code == 404
    assert client.get("/preview/paper.galley-preview.html").status_code == 200
    assert client.get("/preview/paper.qmd").status_code == 404
    for asset in ("galley-app.js", "galley-review.js", "galley-review.css",
                  "00-vendor/codemirror.min.js"):  # fmt: skip
        assert client.get(f"/assets/{asset}").status_code == 200, asset


@pytest.fixture
def env(tmp_path: Path) -> ReviewEnv:
    return make_env(tmp_path)


def test_review_tab_opens_a_session_and_forwards_actions(env: ReviewEnv) -> None:
    bench = Workbench(env.bob_dir, gh_executable=env.bob.executable)
    try:
        bench.handle({"type": "wb_tab", "tab": "review"})
        state = bench.snapshot()
        assert state["review_error"] == "" and state["review"]["pr"]["number"] == 1
        assert bench.file()["path"] == "paper.qmd" and bench.file_epoch() >= 0
        line = next(n for n, text in enumerate(bench.file()["text"].splitlines(), 1)
                    if "last calibrated" in text)  # fmt: skip
        bench.handle({"type": "comment", "line": line, "body": "Calibrated by whom?"})
        assert bench.snapshot()["review"]["threads"][0]["comments"][0]["author"] == "bob"
    finally:
        bench.close_review()
    assert bench.snapshot()["review"] is None


def test_review_tab_explains_when_github_is_unreachable(tmp_path: Path) -> None:
    paper = builders.make_paper(tmp_path / "deposit-review")
    (paper / ".git").mkdir()
    bench = Workbench(tmp_path, gh_executable="definitely-not-gh")
    bench.handle({"type": "wb_tab", "tab": "review"})
    state = bench.snapshot()
    assert state["review"] is None
    assert "Review needs this paper to be a GitHub repository" in state["review_error"]
    assert "gh is not installed" in state["review_error"]


@pytest.mark.tex
def test_convert_build_verify_and_accept_through_the_app(tmp_path: Path) -> None:
    """The whole path a user clicks through: upload, convert, build, verify, accept."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = builders.make_docx(tmp_path / "legacy.docx", builders.make_png(tmp_path / "i.png"))
    bench = Workbench(workspace)

    # Convert (which finishes by verifying).
    bench.convert(str(source), "legacy-paper", "", "https://example.com/doc", wait=True)
    job = bench.snapshot()["job"]
    assert job["state"] == "done", job
    assert job["steps"][0] == "Extracting text, tables, figures and charts"
    assert "Rendering the PDF" in job["steps"] and "Reading the rendered PDF" in job["steps"]
    assert job["summary"].endswith("Verify: PASS.")
    state = bench.snapshot()
    assert state["paper"] == "legacy-paper"
    detail = state["detail"]
    assert detail["source"] == "original.docx" and detail["verify"]["status"] == "pass"
    assert "<h2>Unmapped styles</h2>" in detail["conversion_html"]
    assert detail["pdf"].startswith("/paper/legacy-paper/paper.pdf?v=")
    assert state["papers"][0]["verify"] == "pass" and state["papers"][0]["has_pdf"] is True

    # Build on its own.
    bench.build_paper(wait=True)
    assert bench.job.state == "done" and "all checks passed" in bench.job.summary
    assert bench.snapshot()["detail"]["build"]["ok"] is True

    # An unlisted data file fails the build; the app can add it to the manifest.
    extra = workspace / "legacy-paper" / "data" / "extra.csv"
    extra.write_text("x\n1\n", encoding="utf-8")
    bench.build_paper(wait=True)
    assert bench.job.state == "failed" and "not in manifest: data/extra.csv" in bench.job.summary
    bench.handle({"type": "wb_data_add", "path": "data/extra.csv"})
    assert bench.snapshot()["detail"]["data"]["problems"] == []

    # Break a number: verify fails, and a numeric finding needs a named person.
    paper = workspace / "legacy-paper" / "paper.qmd"
    paper.write_text(paper.read_text("utf-8").replace("12.25%", "12.52%"), encoding="utf-8")
    bench.verify_paper(wait=True)
    # In so short a paper one altered number also drops text coverage below 99.5%.
    assert bench.job.state == "failed" and "Numbers" in bench.job.summary
    assert bench.snapshot()["detail"]["verify"]["status"] == "fail"

    bench.accept("numbers-missing-12.25%", "typo in source", "")
    wait_for_job(bench)
    report = bench.snapshot()["detail"]["verify"]
    assert report["status"] == "fail"
    assert "naming a person" in report["rejected-acceptances"][0]

    for finding in ("numbers-missing-12.25%", "numbers-extra-12.52%"):
        bench.accept(finding, "source figure restated", "Wendi Wang")
        wait_for_job(bench)
    bench.accept("text-coverage", "same restated figure", "")  # not numeric: no name needed
    wait_for_job(bench)
    assert bench.snapshot()["detail"]["verify"]["status"] == "pass"

    client = create_app(bench).server.test_client()
    assert client.get("/paper/legacy-paper/paper.pdf").data.startswith(b"%PDF")
    assert client.get("/paper/legacy-paper/verify-visual/source-image1.png").status_code == 200
    shutil.rmtree(workspace)


def wait_for_job(bench: Workbench) -> None:
    import time

    deadline = time.monotonic() + 180
    while bench.job.state == "running":
        assert time.monotonic() < deadline, "job timed out"
        time.sleep(0.05)
