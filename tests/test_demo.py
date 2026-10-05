"""`galley demo`: the whole process on sample material with a simulated GitHub."""

from __future__ import annotations

from pathlib import Path

import pytest

from galley import demo
from galley.workbench import Workbench


def test_prepare_workspace_writes_the_sample_once(tmp_path: Path) -> None:
    first = demo.prepare_workspace(tmp_path)
    assert Path(first["sample"]).name == "legacy-whitepaper.docx"
    assert Path(first["sample"]).is_file() and "bcbs2013" in Path(first["bib"]).read_text("utf-8")
    stamp = Path(first["sample"]).stat().st_mtime_ns
    assert demo.prepare_workspace(tmp_path) == first
    assert Path(first["sample"]).stat().st_mtime_ns == stamp


@pytest.mark.tex
def test_demo_runs_end_to_end(tmp_path: Path) -> None:
    bench = Workbench(tmp_path, demo=True)
    material = bench.snapshot()["demo"]
    assert material["slug"] == "legacy-whitepaper"

    # Convert the sample: it works, and verify fails for the one reason the demo explains.
    bench.convert(material["sample"], material["slug"], material["bib"], "", wait=True)
    assert bench.job.state == "done", bench.job.error
    assert (
        "1 chart(s) rebuilt" in bench.job.summary
        and "1 unresolved citation(s)" in bench.job.summary
    )
    report = bench.snapshot()["detail"]["verify"]
    failing = {f["id"] for c in report["checks"] if c["status"] == "fail" for f in c["findings"]
               if f["fails"]}  # fmt: skip
    assert "numbers-missing-1999" in failing

    # Review: a pull request appears on the simulated GitHub with the teammate's comments.
    try:
        bench.handle({"type": "wb_tab", "tab": "review"})
        state = bench.snapshot()
        assert state["review_error"] == "", state["review_error"]
        review = state["review"]
        assert review["pr"]["title"] == "Note committee review" and review["viewer"] == "you"
        assert review["on_pr_branch"] is True
        assert sorted(t["kind"] for t in review["threads"]) == ["anchored", "line"]
        assert {t["comments"][0]["author"] for t in review["threads"]} == {"alice"}
        assert review["threads"][0]["comments"][0]["avatar"].startswith("data:image/svg+xml,")

        # You reply; the simulated teammate answers; the next poll shows it.
        thread = review["threads"][0]["id"]
        bench.handle({"type": "reply", "thread": thread, "body": "Checked against table 3."})
        assert bench._teammate is not None
        bench._teammate.stop()  # drive it by hand so the test does not wait on a timer
        assert bench._teammate.answer_once() == 1
        assert bench._teammate.answer_once() == 0  # never answers the same comment twice
        assert bench.review is not None and bench.review.session.refresh() is True
        authors = [c["author"] for c in bench.snapshot()["review"]["threads"][0]["comments"]]
        assert authors == ["alice", "you", "alice"]
        assert bench.snapshot()["review"]["sync"]["new_comments"] == 1

        # Reopening reuses the simulation rather than rebuilding it.
        bench.handle({"type": "wb_tab", "tab": "build"})
        bench.handle({"type": "wb_tab", "tab": "review"})
        assert len(bench.snapshot()["review"]["threads"]) == 2
    finally:
        bench.close_review()
