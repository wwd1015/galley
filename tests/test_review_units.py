from __future__ import annotations

import time
from pathlib import Path

import pytest

from galley.review import anchors, preview
from galley.review.model import Thread, build_threads, initials, locate_threads, render_markdown
from galley.review.sync import Poller

TEXT = """\
---
title: "Paper"
---

# Introduction

Retail balances fell by 4.5%.

```{python}
#| label: fig-a
plot()
```

| A | B |
|---|---|

Wholesale funding reprices faster.
"""


def test_anchor_round_trip_escapes_quotes_and_dashes() -> None:
    line = '  The "stable" rate -- as defined -- is 5% of balances, and it continues for a while.'
    body = anchors.with_anchor("Is this right?", 42, line)
    assert body.startswith('<!-- galley:anchor line=42 quote="')
    assert "--" not in body[4 : body.index("-->")]
    anchor, visible = anchors.parse_anchor(body)
    assert anchor == anchors.Anchor(42, anchors.quote_of(line))
    assert len(anchor.quote) == 60 and visible == "Is this right?"
    assert anchors.parse_anchor("plain comment") == (None, "plain comment")


def test_diff_lines_cover_added_and_context_lines() -> None:
    patch = "@@ -3,4 +3,5 @@ heading\n context\n-old\n+new\n+added\n context\n\\ No newline\n"
    assert anchors.diff_lines(patch) == {3, 4, 5, 6}
    assert anchors.diff_lines("") == set()
    assert anchors.hunk_quote("@@ -1,2 +1,2 @@\n context\n+the commented line") == (
        "the commented line"
    )


def test_locate_prefers_quote_then_line() -> None:
    lines = ["alpha", "Retail balances fell by 4.5%.", "beta", "Retail balances fell by 4.5%."]
    assert anchors.locate(lines, "Retail balances fell by 4.5%.", 4) == 4  # nearest exact match
    assert anchors.locate(lines, "Retail balances fell by 4.5%.", 1) == 2
    assert (
        anchors.locate(
            ["x", "", "Retail balances fell by 4.6%."], "Retail balances fell by 4.5%.", 1
        )
        == 3
    )
    assert anchors.locate(lines, "A sentence that was deleted entirely.", 2) is None  # outdated
    assert anchors.locate(lines, "", 3) == 3  # no quote: trust the line number
    assert anchors.locate(lines, "", 9) is None


def raw(identifier: int, user: str, body: str, **fields: object) -> dict[str, object]:
    return {
        "id": identifier, "user": {"login": user, "avatar_url": f"https://a/{user}"},
        "body": body, "created_at": f"2026-10-05T10:00:{identifier:02d}Z",
        "updated_at": f"2026-10-05T10:00:{identifier:02d}Z", "path": "paper.qmd",
        "html_url": "https://github.com/x", "in_reply_to_id": None, "subject_type": "line",
        "line": None, "original_line": None, "diff_hunk": "", **fields,
    }  # fmt: skip


def test_build_and_locate_threads() -> None:
    comments = [
        raw(1, "bob", "Source?", line=7, diff_hunk="@@ -7 +7 @@\n+Retail balances fell by 4.5%."),
        raw(2, "alice", "Treasury MI.", in_reply_to_id=1),
        raw(
            3,
            "bob",
            anchors.with_anchor("Faster than what?", 16, "Wholesale funding reprices faster."),
            subject_type="file",
        ),
        raw(4, "bob", "General remark", subject_type="file"),
        raw(
            5,
            "bob",
            anchors.with_anchor("Gone", 3, "A deleted sentence here."),
            subject_type="file",
        ),
    ]
    threads = build_threads(comments, {1: {"id": "T1", "resolved": True}}, viewer="alice")
    assert [(t.id, t.kind, len(t.comments)) for t in threads] == [
        (1, "line", 2), (3, "anchored", 1), (4, "file", 1), (5, "anchored", 1),
    ]  # fmt: skip
    assert threads[0].resolved and threads[0].node_id == "T1"
    assert threads[0].comments[1].mine and not threads[0].comments[0].mine
    assert threads[1].comments[0].body == "Faster than what?"  # anchor hidden

    locate_threads(threads, {"paper.qmd": TEXT})
    assert [(t.id, t.line, t.outdated) for t in threads] == [
        (1, 7, False), (3, 17, False), (4, None, False), (5, None, True),
    ]  # fmt: skip
    assert [t.number for t in threads] == [1, 2, 3, 4]
    assert threads[0].as_dict()["initials"] == "BO"


def test_markdown_in_comments_escapes_html() -> None:
    html = render_markdown("**bold** and `code`\n\n<script>alert(1)</script>")
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert initials("wendi-wang") == "WW" and initials("octocat") == "OC"


def thread(identifier: int, line: int | None, number: int, resolved: bool = False) -> Thread:
    built = build_threads([raw(identifier, "wendi-wang", "c")], {}, "x")[0]
    built.line, built.number, built.resolved = line, number, resolved
    return built


def test_prose_lines_exclude_front_matter_code_tables_and_headings() -> None:
    usable = preview.prose_lines(TEXT.split("\n"))
    lines = TEXT.split("\n")
    assert [lines[i] for i, ok in enumerate(usable) if ok] == [
        "Retail balances fell by 4.5%.", "Wholesale funding reprices faster.",
    ]  # fmt: skip


def test_annotate_places_spans_on_prose_only() -> None:
    out = preview.annotate(
        TEXT,
        [thread(1, 7, 1), thread(2, 5, 2, resolved=True), thread(3, 10, 3), thread(4, None, 4)],
    ).split("\n")
    # Line 7 carries its own comment and the one left on the heading above it.
    assert out[6] == (
        "Retail balances fell by 4.5%."
        ' []{.galley-comment ref="c1" n="1" initials="WW" state="open"}'
        ' []{.galley-comment ref="c2" n="2" initials="WW" state="resolved"}'
    )
    # A comment inside the code chunk moves to the next prose line; the chunk is untouched.
    assert out[8:12] == ["```{python}", "#| label: fig-a", "plot()", "```"]
    assert out[16].startswith('Wholesale funding reprices faster. []{.galley-comment ref="c3"')
    assert "c4" not in "\n".join(out)
    assert out[:3] == ["---", 'title: "Paper"', "---"]


def test_stage_writes_copies_and_never_touches_sources(tmp_path: Path) -> None:
    (tmp_path / "sections").mkdir()
    main = "---\ntitle: x\n---\n\nIntro text.\n\n{{< include sections/01-a.qmd >}}\n"
    section = "# A\n\nSection text.\n"
    (tmp_path / "paper.qmd").write_text(main, encoding="utf-8")
    (tmp_path / "sections" / "01-a.qmd").write_text(section, encoding="utf-8")
    in_section = thread(1, 3, 1)
    in_section.path = "sections/01-a.qmd"

    staged = preview.stage(
        tmp_path,
        "paper.qmd",
        {"paper.qmd": main.replace("Intro", "Edited")},
        [in_section, thread(2, 5, 2)],
    )
    assert staged.name == "paper.galley-preview.qmd"
    text = staged.read_text(encoding="utf-8")
    assert 'Edited text. []{.galley-comment ref="c2"' in text  # unsaved editor text is used
    assert "{{< include sections/01-a.galley-preview.qmd >}}" in text
    copy = (tmp_path / "sections" / "01-a.galley-preview.qmd").read_text(encoding="utf-8")
    assert 'Section text. []{.galley-comment ref="c1"' in copy
    assert (tmp_path / "paper.qmd").read_text(encoding="utf-8") == main
    assert (tmp_path / "sections" / "01-a.qmd").read_text(encoding="utf-8") == section


def test_render_command_modes() -> None:
    staged = Path("paper.galley-preview.qmd")
    html = preview.render_command(staged, "html")
    assert html[1:5] == ["render", "paper.galley-preview.qmd", "--profile", "review"]
    assert html[-4:] == ["--to", "html", "--lua-filter",
                         "_extensions/galley/galley/filters/galley-review.lua"]  # fmt: skip
    assert preview.render_command(staged, "pdf")[-2:] == ["--to", "galley-pdf"]
    assert preview.output_name("paper.qmd", "pdf") == "paper.galley-preview.pdf"
    assert preview.output_name("paper.qmd", "html") == "paper.galley-preview.html"


class FakeWorker(preview.PreviewWorker):
    """Counts renders instead of running Quarto."""

    def __init__(self, paper_dir: Path, results: list[int]) -> None:
        super().__init__(paper_dir, lambda: ("paper.qmd", {}, []), debounce=0.08)
        self.results = results
        self.runs = 0

    def _run(self, command: list[str]) -> tuple[int, str]:
        self.runs += 1
        code = self.results.pop(0) if self.results else 0
        return code, "" if code == 0 else "line 1\nERROR: bad chunk"


def wait_for(condition: object, timeout: float = 3.0) -> None:
    assert callable(condition)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_worker_debounces_keystrokes_into_one_render(tmp_path: Path) -> None:
    (tmp_path / "paper.qmd").write_text("Text.\n", encoding="utf-8")
    worker = FakeWorker(tmp_path, [])
    worker.start()
    for _ in range(5):
        worker.request()
        time.sleep(0.01)
    assert worker.status.state == "waiting" and worker.runs == 0
    wait_for(lambda: worker.status.state == "ok")
    assert worker.runs == 1
    assert (worker.status.serial, worker.status.output) == (1, "paper.galley-preview.html")
    worker.stop()


def test_failed_render_keeps_previous_preview(tmp_path: Path) -> None:
    (tmp_path / "paper.qmd").write_text("Text.\n", encoding="utf-8")
    worker = FakeWorker(tmp_path, [0, 1, 0])
    worker.start()
    worker.request(immediate=True)
    wait_for(lambda: worker.status.state == "ok")
    worker.request(immediate=True)
    wait_for(lambda: worker.status.state == "error")
    assert worker.status.error.endswith("ERROR: bad chunk")
    assert (worker.status.serial, worker.status.output) == (1, "paper.galley-preview.html")
    worker.request("pdf", immediate=True)
    wait_for(lambda: worker.status.state == "ok")
    assert (worker.status.serial, worker.status.output) == (2, "paper.galley-preview.pdf")
    assert worker.status.error == ""
    worker.stop()


def test_poller_intervals_and_idle_backoff() -> None:
    polls: list[float] = []
    poller = Poller(lambda: polls.append(time.monotonic()), active=0.03, idle=5.0)
    assert Poller(lambda: None).interval == 10.0
    poller.start()
    wait_for(lambda: len(polls) >= 3)
    poller.set_idle(True)
    assert poller.interval == 5.0
    time.sleep(0.1)
    settled = len(polls)
    time.sleep(0.15)
    assert len(polls) <= settled + 1  # backed off
    poller.set_idle(False)  # activity returns: poll straight away
    wait_for(lambda: len(polls) >= settled + 2)
    poller.stop()
    idle = Poller(lambda: None)
    idle.set_idle(True)
    assert idle.interval == 60.0


def test_poller_survives_a_failing_refresh() -> None:
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1

    poller = Poller(flaky, active=0.02)
    poller.start()
    wait_for(lambda: poller.polls >= 2)
    poller.stop()
    assert calls["n"] >= 2


@pytest.mark.parametrize("name", ["paper.qmd", "sections/01-intro.qmd"])
def test_preview_name(name: str) -> None:
    assert preview.preview_name(name) == name.replace(".qmd", ".galley-preview.qmd")
