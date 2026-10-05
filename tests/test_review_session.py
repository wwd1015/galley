"""The review session against a fake GitHub, with two reviewers on separate clones."""

from __future__ import annotations

from pathlib import Path

import pytest

from galley.review import anchors, preview
from galley.review.app import Controller, build, create_app
from galley.review.preview import PreviewWorker
from galley.review.session import ReviewError, ReviewSession
from galley.review.sync import ACTIVE_SECONDS, Poller

from .review_env import NEW_LINE, ReviewEnv, git, line_of, make_env

OFF_DIFF = "Rates were last calibrated in 2025."


@pytest.fixture
def env(tmp_path: Path) -> ReviewEnv:
    return make_env(tmp_path)


def opened(env: ReviewEnv, who: str) -> ReviewSession:
    session = env.session(who)
    session.select_pr(1)
    return session


def raw_bodies(env: ReviewEnv) -> list[str]:
    comments = env.read_state()["comments"]
    assert isinstance(comments, list)
    return [str(c["body"]) for c in comments]


def test_start_and_select_pr(env: ReviewEnv) -> None:
    session = env.session("bob")
    assert (session.viewer, session.repo) == ("bob", "acme/deposit-review")
    assert [pr["number"] for pr in session.prs] == [1]
    session.select_pr(1)
    snapshot = session.snapshot()
    assert snapshot["pr"]["branch"] == "edits" and snapshot["on_pr_branch"] is True
    assert snapshot["files"] == ["paper.qmd"] and snapshot["path"] == "paper.qmd"
    assert snapshot["threads"] == [] and snapshot["sync"]["error"] == ""
    assert line_of(env.bob_dir, NEW_LINE) in session.diff["paper.qmd"]


def test_comment_inside_the_diff_is_a_native_line_comment(env: ReviewEnv) -> None:
    bob = opened(env, "bob")
    line = line_of(env.bob_dir, NEW_LINE)
    bob.add_comment(line, "Where does **4.8%** come from?")
    (thread,) = bob.snapshot()["threads"]
    assert (thread["kind"], thread["line"], thread["n"]) == ("line", line, 1)
    assert "<strong>4.8%</strong>" in thread["comments"][0]["body_html"]
    assert raw_bodies(env) == ["Where does **4.8%** come from?"]  # no anchor needed


def test_comment_outside_the_diff_becomes_an_anchored_file_comment(env: ReviewEnv) -> None:
    """The main technical risk: GitHub refuses line comments outside the diff."""
    bob = opened(env, "bob")
    line = line_of(env.bob_dir, OFF_DIFF)
    assert line not in bob.diff["paper.qmd"]
    bob.add_comment(line, "Calibrated by whom?")

    (body,) = raw_bodies(env)
    assert body == f'<!-- galley:anchor line={line} quote="{OFF_DIFF}" -->\nCalibrated by whom?'
    comments = env.read_state()["comments"]
    assert isinstance(comments, list) and comments[0]["subject_type"] == "file"
    # The app shows it as an ordinary line comment, with the anchor hidden.
    (thread,) = bob.snapshot()["threads"]
    assert (thread["kind"], thread["line"]) == ("anchored", line)
    assert thread["comments"][0]["body"] == "Calibrated by whom?"


def test_unpushed_edits_force_an_anchor_even_inside_the_diff(env: ReviewEnv) -> None:
    bob = opened(env, "bob")
    text = (env.bob_dir / "paper.qmd").read_text(encoding="utf-8")
    bob.edit("paper.qmd", text.replace("# Introduction", "# Introduction\n\nA new opening line."))
    line = line_of(env.bob_dir, NEW_LINE)
    assert not bob.can_comment_natively("paper.qmd", line)
    bob.add_comment(line, "Check this.")
    assert raw_bodies(env)[0].startswith(f"<!-- galley:anchor line={line} ")
    assert bob.snapshot()["threads"][0]["line"] == line


def test_two_reviewers_see_each_other_within_one_poll(env: ReviewEnv) -> None:
    """Phase 5 acceptance, on one machine: each reviewer sees the other's comment and
    reply in both panes after a single poll, and polls run every 10 seconds."""
    alice, bob = opened(env, "alice"), opened(env, "bob")
    line = line_of(env.bob_dir, OFF_DIFF)
    bob.add_comment(line, "Calibrated by whom?")

    assert alice.refresh() is True  # one poll
    (seen,) = alice.snapshot()["threads"]
    assert seen["comments"][0]["author"] == "bob" and seen["line"] == line  # source pane
    assert alice.snapshot()["sync"]["new_comments"] == 1
    staged = preview.stage(env.alice_dir, "paper.qmd", {}, alice.threads)  # preview pane
    assert (
        f'{OFF_DIFF} []{{.galley-comment ref="{seen["ref"]}" n="1" initials="BO" state="open"}}'
        in (staged.read_text(encoding="utf-8"))
    )

    alice.reply(seen["id"], "Risk Analytics, in Q2.")
    assert bob.refresh() is True
    (thread,) = bob.snapshot()["threads"]
    assert [c["author"] for c in thread["comments"]] == ["bob", "alice"]
    assert [c["mine"] for c in thread["comments"]] == [True, False]
    assert bob.snapshot()["sync"]["new_comments"] == 1
    assert ACTIVE_SECONDS <= 15

    # Nothing changed since: the next poll is a 304 and reports no change.
    assert bob.refresh() is False
    calls = env.read_state()["calls"]
    assert isinstance(calls, list) and "If-None-Match" in " ".join(calls[-4:])
    bob.mark_seen()
    assert bob.snapshot()["sync"]["new_comments"] == 0


def test_resolve_edit_and_delete(env: ReviewEnv) -> None:
    alice, bob = opened(env, "alice"), opened(env, "bob")
    line = line_of(env.bob_dir, OFF_DIFF)
    bob.add_comment(line, "Calibrated by whom?")
    thread_id = bob.snapshot()["threads"][0]["id"]

    bob.edit_comment(thread_id, "Calibrated by whom, and when?")
    assert raw_bodies(env)[0].startswith("<!-- galley:anchor ")  # the anchor survives an edit
    assert bob.snapshot()["threads"][0]["comments"][0]["body"] == "Calibrated by whom, and when?"

    alice.refresh()
    with pytest.raises(ReviewError, match="only edit your own"):
        alice.edit_comment(thread_id, "hijack")
    with pytest.raises(ReviewError, match="only delete your own"):
        alice.delete_comment(thread_id)

    alice.set_resolved(thread_id, True)
    bob.refresh()
    assert bob.snapshot()["threads"][0]["resolved"] is True
    assert bob.snapshot()["open_threads"] == 0
    bob.set_resolved(thread_id, False)
    assert bob.snapshot()["threads"][0]["resolved"] is False

    bob.delete_comment(thread_id)
    assert bob.snapshot()["threads"] == []
    alice.refresh()
    assert alice.snapshot()["threads"] == []


def test_comments_follow_their_text_and_go_outdated(env: ReviewEnv) -> None:
    bob = opened(env, "bob")
    line = line_of(env.bob_dir, OFF_DIFF)
    bob.add_comment(line, "Calibrated by whom?")
    text = (env.bob_dir / "paper.qmd").read_text(encoding="utf-8")

    bob.edit("paper.qmd", text.replace("# Introduction", "# Introduction\n\nNew line.\n\nAnother."))
    assert bob.snapshot()["threads"][0]["line"] == line + 4  # moved with its text

    bob.edit("paper.qmd", text.replace(OFF_DIFF, "Rates were last calibrated in 2025, by RA."))
    assert bob.snapshot()["threads"][0]["line"] == line  # small edit: still found

    bob.edit("paper.qmd", text.replace(OFF_DIFF, "This paragraph was rewritten from scratch."))
    thread = bob.snapshot()["threads"][0]
    assert thread["outdated"] is True and thread["line"] is None  # shown as outdated, like GitHub


def test_validation_errors(env: ReviewEnv) -> None:
    session = env.session("bob")
    with pytest.raises(ReviewError, match="Select a pull request"):
        session.add_comment(1, "x")
    session.select_pr(1)
    with pytest.raises(ReviewError, match="Write a comment"):
        session.add_comment(1, "   ")
    with pytest.raises(ReviewError, match="Line 9999 is not in"):
        session.add_comment(9999, "x")
    with pytest.raises(ReviewError, match="no longer exists"):
        session.reply(12345, "x")
    with pytest.raises(ReviewError, match="not part of this pull request"):
        session.select_file("other.qmd")


def test_commit_push_then_teammate_pulls(env: ReviewEnv) -> None:
    alice, bob = opened(env, "alice"), opened(env, "bob")
    text = (env.alice_dir / "paper.qmd").read_text(encoding="utf-8")
    assert alice.snapshot()["dirty"] is False
    alice.edit("paper.qmd", text.replace("in 2025.", "in 2025 and reviewed in 2026."))
    assert alice.snapshot()["dirty"] is True
    alice.commit_and_push()
    assert "Committed and pushed: review: edit paper.qmd (PR #1)" in alice.message["text"]
    assert alice.snapshot()["dirty"] is False
    env.publish_head(env.alice_dir)

    assert bob.refresh() is True
    assert bob.snapshot()["sync"]["head_changed"] is True  # banner: "Pull and refresh"
    epoch = bob.file_epoch
    bob.pull()
    assert "reviewed in 2026" in (env.bob_dir / "paper.qmd").read_text(encoding="utf-8")
    assert bob.snapshot()["sync"]["head_changed"] is False
    assert bob.file_epoch == epoch + 1  # the editor reloads the file


def test_pull_never_overwrites_local_edits(env: ReviewEnv) -> None:
    from galley.review.git import GitError

    alice, bob = opened(env, "alice"), opened(env, "bob")
    alice_text = (env.alice_dir / "paper.qmd").read_text(encoding="utf-8")
    alice.edit("paper.qmd", alice_text.replace("in 2025.", "in 2025 (A)."))
    alice.commit_and_push()
    env.publish_head(env.alice_dir)

    mine = (
        (env.bob_dir / "paper.qmd").read_text(encoding="utf-8").replace("in 2025.", "in 2025 (B).")
    )
    bob.edit("paper.qmd", mine)
    with pytest.raises(GitError, match="nothing was overwritten"):
        bob.pull()
    assert (env.bob_dir / "paper.qmd").read_text(encoding="utf-8") == mine

    # Committing first turns it into an ordinary merge conflict, reported plainly.
    git(env.bob_dir, "commit", "-q", "-am", "local edit")
    with pytest.raises(GitError, match=r"merge conflicts in: paper\.qmd"):
        bob.pull()


def test_sync_errors_are_reported_not_raised(env: ReviewEnv) -> None:
    bob = opened(env, "bob")
    env.state.write_text("{not json", encoding="utf-8")
    assert bob.refresh() is True
    assert bob.snapshot()["sync"]["error"]


def controller_for(env: ReviewEnv, who: str = "bob") -> Controller:
    session = opened(env, who)
    worker = PreviewWorker(session.repo_dir, lambda: ("paper.qmd", {}, list(session.threads)))
    return Controller(session, worker, Poller(lambda: None))


def test_controller_applies_actions_and_reports_errors(env: ReviewEnv) -> None:
    controller = controller_for(env)
    line = line_of(env.bob_dir, OFF_DIFF)
    before = controller.version()
    controller.handle({"type": "comment", "line": line, "body": "Calibrated by whom?"})
    state = controller.snapshot()
    assert controller.version() != before
    assert state["threads"][0]["comments"][0]["body"] == "Calibrated by whom?"
    assert state["render"]["state"] == "waiting"  # markers changed: a render is queued

    thread = state["threads"][0]["id"]
    controller.handle({"type": "reply", "thread": thread, "body": "Me."})
    controller.handle({"type": "resolve", "thread": thread, "resolved": True})
    assert controller.snapshot()["threads"][0]["resolved"] is True
    controller.handle({"type": "submit_review", "event": "APPROVE", "body": "ok"})
    assert controller.snapshot()["message"]["text"] == "Approval submitted."

    controller.handle({"type": "reply", "thread": 999, "body": "x"})
    message = controller.snapshot()["message"]
    assert (message["kind"], message["text"]) == ("error", "That comment thread no longer exists.")
    controller.handle({"type": "comment"})
    assert "Malformed request" in controller.snapshot()["message"]["text"]

    text = (env.bob_dir / "paper.qmd").read_text(encoding="utf-8")
    controller.handle({"type": "edit", "path": "paper.qmd", "text": text + "\nMore.\n"})
    assert (env.bob_dir / "paper.qmd").read_text(encoding="utf-8").endswith("More.\n")
    controller.handle({"type": "preview_mode", "mode": "pdf"})
    assert controller.snapshot()["render"]["mode"] == "pdf"
    controller.handle({"type": "activity", "idle": True})
    assert controller.poller.idle is True
    assert controller.file()["path"] == "paper.qmd"


def test_app_serves_page_and_only_preview_files(env: ReviewEnv) -> None:
    controller = controller_for(env)
    app = create_app(controller, env.bob_dir)
    (env.bob_dir / "paper.galley-preview.html").write_text("<p>preview</p>", encoding="utf-8")
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout").get_json()
    assert "gl-editor" in str(layout) and "store-action" in str(layout)
    served = client.get("/preview/paper.galley-preview.html")
    assert served.status_code == 200 and served.headers["Cache-Control"] == "no-store"
    assert client.get("/preview/paper.qmd").status_code == 404
    assert client.get("/preview/.git/config").status_code == 404
    for asset in ("galley-review.js", "galley-review.css", "00-vendor/codemirror.min.js"):
        assert client.get(f"/assets/{asset}").status_code == 200, asset


def test_build_selects_the_only_pull_request(env: ReviewEnv) -> None:
    app, controller = build(env.bob_dir, env.bob)
    try:
        assert controller.session.pr is not None and controller.session.pr["number"] == 1
        assert app.title == "Galley Review"
    finally:
        controller.preview.stop()
        controller.poller.stop()


@pytest.mark.tex
def test_comments_appear_in_html_and_pdf_previews(env: ReviewEnv) -> None:
    """A comment shows as a margin note in the fast preview and in the PDF proof."""
    import time

    import pymupdf

    bob = opened(env, "bob")
    bob.add_comment(line_of(env.bob_dir, OFF_DIFF), "Calibrated by whom?")
    bob.add_comment(line_of(env.bob_dir, NEW_LINE), "Source?")
    # Threads are numbered in reading order, so the later comment (c101) is note 1.
    bob.set_resolved(bob.snapshot()["threads"][0]["id"], True)
    source_before = (env.bob_dir / "paper.qmd").read_text(encoding="utf-8")
    worker = PreviewWorker(env.bob_dir, lambda: ("paper.qmd", {}, list(bob.threads)), debounce=0.05)
    worker.start()

    def render(mode: str) -> Path:
        serial = worker.status.serial
        worker.request(mode, immediate=True)
        deadline = time.monotonic() + 180
        while worker.status.serial == serial and worker.status.state != "error":
            assert time.monotonic() < deadline, "render timed out"
            time.sleep(0.1)
        assert worker.status.state == "ok", worker.status.error
        return env.bob_dir / worker.status.output

    try:
        html = render("html").read_text(encoding="utf-8")
        assert 'class="galley-note galley-resolved" data-ref="c101"' in html
        assert 'class="galley-note galley-open" data-ref="c100"' in html
        with pymupdf.open(render("pdf")) as pdf:
            text = "".join(page.get_text() for page in pdf)
        assert "[1]" in text and "[2]" in text and "BO" in text
    finally:
        worker.stop()
    assert (env.bob_dir / "paper.qmd").read_text(encoding="utf-8") == source_before
    assert anchors.parse_anchor(source_before)[0] is None
