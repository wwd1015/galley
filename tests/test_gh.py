from __future__ import annotations

from pathlib import Path

import pytest

from galley.gh import Gh, GhError, parse_http

from .review_env import NEW_LINE, ReviewEnv, line_of, make_env


@pytest.fixture
def env(tmp_path: Path) -> ReviewEnv:
    return make_env(tmp_path)


def test_parse_http() -> None:
    response = parse_http('HTTP/2.0 200 OK\r\nEtag: W/"abc"\r\nX: y\r\n\r\n[{"id": 1}]')
    assert (response.status, response.etag, response.body) == (200, 'W/"abc"', [{"id": 1}])
    assert parse_http('HTTP/2.0 304 Not Modified\nEtag: "x"\n\n').not_modified
    with pytest.raises(GhError, match="unexpected output"):
        parse_http("garbage")


def test_identity_and_pull_requests(env: ReviewEnv) -> None:
    assert env.alice.viewer() == "alice" and env.bob.viewer() == "bob"
    assert env.alice.repo() == "acme/deposit-review"
    prs = env.alice.list_prs()
    assert [(p["number"], p["branch"]) for p in prs] == [(1, "edits")]  # PR 2 has no .qmd
    assert env.alice.pr(1).body["head"]["ref"] == "edits"
    assert [f["filename"] for f in env.alice.pr_files(1)] == ["paper.qmd"]


def test_conditional_requests_use_etags(env: ReviewEnv) -> None:
    first = env.alice.comments_changed(1, "")
    assert first.status == 200 and first.etag
    assert env.alice.comments_changed(1, first.etag).not_modified
    line = line_of(env.alice_dir, NEW_LINE)
    env.bob.create_line_comment(1, "sha", "paper.qmd", line, "Source?")
    changed = env.alice.comments_changed(1, first.etag)
    assert changed.status == 200 and changed.etag != first.etag
    calls = env.read_state()["calls"]
    assert isinstance(calls, list) and any("If-None-Match" in call for call in calls)


def test_line_comments_are_refused_outside_the_diff(env: ReviewEnv) -> None:
    with pytest.raises(GhError, match=r"422.*part of the diff"):
        env.alice.create_line_comment(1, "sha", "paper.qmd", 1, "Title?")
    comment = env.alice.create_file_comment(1, "sha", "paper.qmd", "On the file")
    assert comment["subject_type"] == "file"


def test_reply_edit_delete_resolve_and_review(env: ReviewEnv) -> None:
    line = line_of(env.alice_dir, NEW_LINE)
    root = env.bob.create_line_comment(1, "sha", "paper.qmd", line, "Source?")
    reply = env.alice.reply(1, root["id"], "Treasury MI.")
    assert reply["in_reply_to_id"] == root["id"]
    assert env.alice.edit_comment(reply["id"], "Treasury MI, Sept.")["body"] == "Treasury MI, Sept."

    threads = env.alice.review_threads(1)
    assert threads == {root["id"]: {"id": f"PRRT_{root['id']}", "resolved": False}}
    env.alice.set_resolved(threads[root["id"]]["id"], True)
    assert env.bob.review_threads(1)[root["id"]]["resolved"] is True
    env.alice.set_resolved(threads[root["id"]]["id"], False)
    assert env.bob.review_threads(1)[root["id"]]["resolved"] is False

    env.alice.delete_comment(reply["id"])
    assert [c["id"] for c in env.alice.comments(1)] == [root["id"]]

    env.bob.submit_review(1, "APPROVE", "Looks right.")
    assert env.read_state()["reviews"] == [
        {"pr": 1, "user": "bob", "event": "APPROVE", "body": "Looks right."}
    ]
    with pytest.raises(GhError, match="unknown review event"):
        env.bob.submit_review(1, "LGTM")


def test_create_pr_and_hostname(env: ReviewEnv, tmp_path: Path) -> None:
    assert env.alice.create_pr("Review: edits") == 3
    enterprise = Gh(env.alice_dir, hostname="github.example.com", executable=env.alice.executable)
    assert enterprise.viewer() == "alice"
    calls = env.read_state()["calls"]
    assert isinstance(calls, list) and "--hostname github.example.com" in calls[-1]


def test_missing_gh_is_reported(tmp_path: Path) -> None:
    with pytest.raises(GhError, match="gh is not installed"):
        Gh(tmp_path, executable="definitely-not-gh").viewer()
