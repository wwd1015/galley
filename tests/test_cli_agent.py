"""The command line as another program uses it: --json everywhere, stable exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from galley.cli import app

from . import builders
from .review_env import NEW_LINE, ReviewEnv, line_of, make_env

runner = CliRunner()


def run(*args: str, code: int = 0, env: dict[str, str] | None = None) -> dict[str, Any]:
    result = runner.invoke(app, [*args, "--json"], env=env)
    assert result.exit_code == code, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_commands_catalogue_describes_every_command() -> None:
    catalogue = run("commands")
    names = {entry["command"] for entry in catalogue["commands"]}
    assert {"galley convert", "galley build", "galley verify", "galley pr comment",
            "galley status", "galley accept"} <= names  # fmt: skip
    assert catalogue["exit_codes"]["2"].startswith("could not run")
    # Every command that produces a result can be asked for JSON.
    interactive = {"galley app", "galley demo", "galley review"}
    template = {c for c in names if c.startswith("galley template")}
    for entry in catalogue["commands"]:
        if entry["command"] in interactive | template:
            continue
        flags = [flag for p in entry["parameters"] for flag in p["flags"]]
        assert "--json" in flags, entry["command"]
    convert = next(e for e in catalogue["commands"] if e["command"] == "galley convert")
    out = next(p for p in convert["parameters"] if p["name"] == "out")
    assert out["required"] is True and out["flags"] == ["--out"]


def test_new_status_and_data_as_json(tmp_path: Path) -> None:
    created = run("new", "paper-a", "--dir", str(tmp_path), "--no-git")
    assert created == {
        "ok": True,
        "paper": str((tmp_path / "paper-a").resolve()),
        "slug": "paper-a",
    }

    listed = run("status", str(tmp_path))
    assert [p["slug"] for p in listed["papers"]] == ["paper-a"]
    assert listed["papers"][0]["build_ok"] is None and listed["papers"][0]["git"] is False
    assert run("status", str(tmp_path / "paper-a"))["papers"][0]["slug"] == "paper-a"

    paper = tmp_path / "paper-a"
    assert run("data", "verify", str(paper)) == {"ok": True, "problems": []}
    (paper / "data" / "extra.csv").write_text("x\n", encoding="utf-8")
    failed = run("data", "verify", str(paper), code=1)
    assert failed == {"ok": False, "problems": ["not in manifest: data/extra.csv"]}
    added = run("data", "add", "data/extra.csv", "--paper", str(paper), "--source", "test")
    assert added["entry"]["path"] == "data/extra.csv" and len(added["entry"]["sha256"]) == 64


def test_errors_are_json_with_the_exit_code(tmp_path: Path) -> None:
    bad = run("new", "Bad Slug", "--dir", str(tmp_path), code=1)
    assert bad == {"ok": False, "exit_code": 1,
                   "error": "slug must be lowercase letters, digits and hyphens"}  # fmt: skip
    missing = run("verify", str(tmp_path / "none.docx"), str(tmp_path), code=2)
    assert missing["ok"] is False and missing["exit_code"] == 2
    assert run("convert", str(tmp_path / "a.txt"), "--out", str(tmp_path / "o"), code=2)["error"]
    # Without --json the error goes to stderr and stdout stays empty.
    plain = runner.invoke(app, ["new", "Bad Slug", "--dir", str(tmp_path)])
    assert plain.exit_code == 1 and plain.stdout == "" and "error:" in plain.stderr


def test_accept_records_a_finding(tmp_path: Path) -> None:
    paper = builders.make_paper(tmp_path / "paper")
    done = run("accept", "text-1a2b3c4d", str(paper), "--reason", "formatting only")
    assert done["id"] == "text-1a2b3c4d"
    assert "text-1a2b3c4d" in (paper / "verify-accept.yaml").read_text(encoding="utf-8")
    assert run("accept", "text-x", str(paper), "--reason", " ", code=1)["ok"] is False


@pytest.fixture
def env(tmp_path: Path) -> ReviewEnv:
    return make_env(tmp_path)


def test_review_a_pull_request_from_the_command_line(env: ReviewEnv) -> None:
    gh = {"GALLEY_GH": env.bob.executable}
    paper = ["--paper", str(env.bob_dir)]

    listed = run("pr", "list", *paper, env=gh)
    assert [p["number"] for p in listed["pull_requests"]] == [1]

    assert run("pr", "threads", *paper, env=gh)["threads"] == []
    inside = line_of(env.bob_dir, NEW_LINE)
    outside = line_of(env.bob_dir, "Rates were last calibrated")
    run("pr", "comment", "--line", str(inside), "--body", "Source?", *paper, env=gh)
    after = run("pr", "comment", "--line", str(outside), "--body", "By whom?", *paper, "--pr", "1",
                env=gh)  # fmt: skip
    assert after["pr"]["number"] == 1 and after["file"] == "paper.qmd"
    kinds = {t["line"]: t["kind"] for t in after["threads"]}
    assert kinds == {inside: "line", outside: "anchored"}
    first = after["threads"][0]
    assert first["comments"][0] == {
        "id": first["id"], "author": "bob", "body": "Source?", "mine": True,
        "created": first["comments"][0]["created"], "edited": False,
        "url": first["comments"][0]["url"],
    }  # fmt: skip

    # Another reviewer answers and resolves, through the same commands.
    alice = {"GALLEY_GH": env.alice.executable}
    theirs = ["--paper", str(env.alice_dir)]
    replied = run("pr", "reply", str(first["id"]), "--body", "Treasury MI.", *theirs, env=alice)
    assert [c["author"] for c in replied["threads"][0]["comments"]] == ["bob", "alice"]
    assert run("pr", "resolve", str(first["id"]), *theirs, env=alice)["threads"][0]["resolved"]
    assert not run("pr", "resolve", str(first["id"]), "--undo", *paper, env=gh)["threads"][0][
        "resolved"
    ]

    edited = run("pr", "edit", str(first["id"]), "--body", "Source, please?", *paper, env=gh)
    assert edited["threads"][0]["comments"][0]["body"] == "Source, please?"
    refused = run("pr", "edit", str(first["id"]), "--body", "x", *theirs, env=alice, code=1)
    assert refused["error"] == "You can only edit your own comments."
    assert len(run("pr", "delete", str(first["id"]), *paper, env=gh)["threads"]) == 1

    run("pr", "submit", "--event", "request-changes", "--body", "See comments.", *paper, env=gh)
    assert env.read_state()["reviews"] == [
        {"pr": 1, "user": "bob", "event": "REQUEST_CHANGES", "body": "See comments."}
    ]

    # Human-readable output for the same data.
    text = runner.invoke(app, ["pr", "threads", *paper], env=gh).stdout
    assert f"paper.qmd line {outside} (open)" in text and "bob: By whom?" in text


def test_pr_commands_report_unreachable_github(tmp_path: Path) -> None:
    paper = builders.make_paper(tmp_path / "paper")
    failed = run("pr", "threads", "--paper", str(paper), code=2, env={"GALLEY_GH": "no-such-gh"})
    assert "gh is not installed" in failed["error"]


@pytest.mark.tex
def test_convert_build_and_verify_as_json(tmp_path: Path) -> None:
    source = builders.make_docx(tmp_path / "legacy.docx", builders.make_png(tmp_path / "i.png"),
                                chart=True)  # fmt: skip
    out = tmp_path / "paper"
    converted = run("convert", str(source), "--out", str(out))
    assert converted["ok"] is True and converted["verify"] == "pass"
    assert converted["tables"] == ["data/tables/tbl-1.csv"]
    assert converted["charts_rebuilt"] == ["fig-2"] and len(converted["figures_needing_data"]) == 1
    assert converted["verify_report"]["status"] == "pass"

    built = run("build", str(out))
    assert built["ok"] is True and built["pdf"].endswith("paper.pdf")
    assert built["report"]["output"]["pages"] >= 2

    paper = out / "paper.qmd"
    paper.write_text(paper.read_text("utf-8").replace("12.25%", "12.52%"), encoding="utf-8")
    verified = run("verify", str(out / "source" / "original.docx"), str(out), code=1)
    assert verified["ok"] is False
    ids = {f["id"] for c in verified["report"]["checks"] for f in c["findings"]}
    assert "numbers-missing-12.25%" in ids
    assert run("status", str(out))["papers"][0]["verify"] == "fail"
    assert run("doctor", str(out))["versions"]["quarto"]
