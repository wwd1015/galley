#!/usr/bin/env python3
"""A stand-in for the ``gh`` CLI backed by a JSON file, for `galley demo` and for tests.

It implements exactly the calls ``galley.gh`` makes, with GitHub's rules that
matter to Galley: line comments are refused outside the diff, list responses
carry an ETag and honour ``If-None-Match``, and threads resolve through GraphQL.

State file (path in ``FAKE_GH_STATE``): ``{"repo", "user", "prs": {number: {...}},
"comments": [...], "threads": {root id: {"id", "resolved"}}, "next_id", "calls"}``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

AVATAR_COLOURS = ("#0969da", "#8250df", "#bf3989", "#1a7f37", "#9a6700", "#cf222e")


def state_path() -> Path:
    return Path(os.environ["FAKE_GH_STATE"])


def avatar(user: str) -> str:
    """A coloured disc with the user's initial, as a data URI (nothing is fetched)."""
    colour = AVATAR_COLOURS[sum(map(ord, user)) % len(AVATAR_COLOURS)]
    letter = (user[:1] or "?").upper()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56">'
        f'<circle cx="28" cy="28" r="28" fill="{colour}"/>'
        '<text x="28" y="37" font-size="26" font-family="Helvetica,Arial,sans-serif" '
        f'fill="#fff" text-anchor="middle">{letter}</text></svg>'
    )
    return "data:image/svg+xml," + urllib.parse.quote(svg)


def load() -> dict[str, Any]:
    return json.loads(state_path().read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save(state: dict[str, Any]) -> None:
    state_path().write_text(json.dumps(state, indent=1), encoding="utf-8")


def respond(status: int, body: Any, etag: str = "") -> NoReturn:
    reason = {200: "OK", 201: "Created", 204: "No Content", 304: "Not Modified",
              404: "Not Found", 422: "Unprocessable Entity"}[status]  # fmt: skip
    sys.stdout.write(f"HTTP/2.0 {status} {reason}\n")
    if etag:
        sys.stdout.write(f"Etag: {etag}\n")
    sys.stdout.write("Content-Type: application/json\n\n")
    if body is not None:
        sys.stdout.write(json.dumps(body))
    sys.exit(0 if status < 400 else 1)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def diff_lines(patch: str) -> set[int]:
    lines: set[int] = set()
    current = 0
    for row in patch.splitlines():
        header = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", row)
        if header:
            current = int(header.group(1))
        elif row.startswith("-"):
            continue
        elif row.startswith(("+", " ")):
            lines.add(current)
            current += 1
    return lines


def new_comment(state: dict[str, Any], user: str, fields: dict[str, Any]) -> dict[str, Any]:
    identifier = state["next_id"]
    state["next_id"] += 1
    comment = {
        "id": identifier,
        "node_id": f"PRRC_{identifier}",
        "user": {"login": user, "avatar_url": avatar(user)},
        "created_at": now(),
        "updated_at": now(),
        "html_url": f"https://github.com/{state['repo']}/pull/1#discussion_r{identifier}",
        "in_reply_to_id": None,
        "line": None,
        "original_line": None,
        "diff_hunk": "",
        "subject_type": "line",
        **fields,
    }
    state["comments"].append(comment)
    return comment


def api(
    state: dict[str, Any], user: str, method: str, path: str, payload: Any, etag: str
) -> NoReturn:
    path, _, query = path.partition("?")
    repo = state["repo"]
    if path == "user":
        respond(200, {"login": user})
    if path == "graphql":
        graphql(state, payload)
    found = re.fullmatch(rf"repos/{repo}/pulls/(\d+)", path)
    if found:
        pr = state["prs"].get(found.group(1))
        if pr is None:
            respond(404, {"message": "Not Found"})
        body = {"number": int(found.group(1)), "title": pr["title"], "state": "open",
                "html_url": f"https://github.com/{repo}/pull/{found.group(1)}",
                "head": {"sha": pr["head_sha"], "ref": pr["branch"]}}  # fmt: skip
        tag = '"' + hashlib.sha1(json.dumps(body).encode()).hexdigest() + '"'
        if etag == tag:
            respond(304, None, tag)
        respond(200, body, tag)
    found = re.fullmatch(rf"repos/{repo}/pulls/(\d+)/files", path)
    if found:
        respond(200, state["prs"][found.group(1)]["files"])
    found = re.fullmatch(rf"repos/{repo}/pulls/(\d+)/comments", path)
    if found and method == "GET":
        comments = [c for c in state["comments"] if c["pr"] == int(found.group(1))]
        if "sort=updated" in query:
            comments = sorted(comments, key=lambda c: (c["updated_at"], c["id"]), reverse=True)
        tag = '"' + hashlib.sha1(json.dumps(comments).encode()).hexdigest() + '"'
        if etag == tag:
            respond(304, None, tag)
        respond(200, comments, tag)
    if found and method == "POST":
        number = found.group(1)
        files = {f["filename"]: f for f in state["prs"][number]["files"]}
        target = files.get(payload["path"])
        if target is None:
            respond(422, {"message": "Validation Failed: path is not part of the pull request"})
        if payload.get("subject_type") == "file":
            comment = new_comment(
                state,
                user,
                {
                    "pr": int(number),
                    "path": payload["path"],
                    "body": payload["body"],
                    "subject_type": "file",
                },
            )
        else:
            if payload["line"] not in diff_lines(target.get("patch", "")):
                respond(422, {"message": "Validation Failed: line must be part of the diff"})
            comment = new_comment(state, user, {
                "pr": int(number), "path": payload["path"], "body": payload["body"],
                "line": payload["line"], "original_line": payload["line"],
                "diff_hunk": target.get("patch", ""),
            })  # fmt: skip
        state["threads"][str(comment["id"])] = {"id": f"PRRT_{comment['id']}", "resolved": False}
        save(state)
        respond(201, comment)
    found = re.fullmatch(rf"repos/{repo}/pulls/(\d+)/comments/(\d+)/replies", path)
    if found:
        parent: Any = next((c for c in state["comments"] if c["id"] == int(found.group(2))), None)
        if parent is None:
            respond(404, {"message": "Not Found"})
        comment = new_comment(state, user, {
            "pr": int(found.group(1)), "path": parent["path"], "body": payload["body"],
            "in_reply_to_id": parent["id"], "line": parent["line"],
            "original_line": parent["original_line"], "subject_type": parent["subject_type"],
        })  # fmt: skip
        save(state)
        respond(201, comment)
    found = re.fullmatch(rf"repos/{repo}/pulls/comments/(\d+)", path)
    if found:
        existing: Any = next((c for c in state["comments"] if c["id"] == int(found.group(1))), None)
        if existing is None:
            respond(404, {"message": "Not Found"})
        if method == "DELETE":
            state["comments"] = [
                c for c in state["comments"]
                if c["id"] != existing["id"] and c["in_reply_to_id"] != existing["id"]
            ]  # fmt: skip
            state["threads"].pop(str(existing["id"]), None)
            save(state)
            respond(204, None)
        existing["body"] = payload["body"]
        existing["updated_at"] = now()
        save(state)
        respond(200, existing)
    found = re.fullmatch(rf"repos/{repo}/pulls/(\d+)/reviews", path)
    if found:
        state.setdefault("reviews", []).append({"pr": int(found.group(1)), "user": user, **payload})
        save(state)
        respond(200, {"id": len(state["reviews"])})
    respond(404, {"message": f"fake gh: unhandled {method} {path}"})


def graphql(state: dict[str, Any], payload: dict[str, Any]) -> NoReturn:
    query, variables = payload["query"], payload["variables"]
    if "reviewThreads" in query:
        number = variables["number"]
        roots = {
            c["id"] for c in state["comments"] if c["pr"] == number and not c["in_reply_to_id"]
        }
        nodes = [
            {"id": thread["id"], "isResolved": thread["resolved"],
             "comments": {"nodes": [{"databaseId": int(root)}]}}
            for root, thread in state["threads"].items() if int(root) in roots
        ]  # fmt: skip
        data = {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}  # fmt: skip
        sys.stdout.write(json.dumps({"data": data}))
        sys.exit(0)
    for thread in state["threads"].values():
        if thread["id"] == variables["id"]:
            thread["resolved"] = "unresolveReviewThread" not in query
    save(state)
    sys.stdout.write(json.dumps({"data": {"ok": True}}))
    sys.exit(0)


def main(argv: list[str]) -> None:
    state = load()
    user = os.environ.get("FAKE_GH_USER", "") or state["user"]
    state.setdefault("calls", []).append(" ".join(argv))
    save(state)
    if argv[:2] == ["repo", "view"]:
        print(json.dumps({"nameWithOwner": state["repo"]}))
        return
    if argv[:2] == ["pr", "list"]:
        print(json.dumps([
            {"number": int(n), "title": pr["title"], "headRefName": pr["branch"],
             "author": {"login": pr.get("author", user)},
             "url": f"https://github.com/{state['repo']}/pull/{n}",
             "files": [{"path": f["filename"]} for f in pr["files"]]}
            for n, pr in state["prs"].items()
        ]))  # fmt: skip
        return
    if argv[:2] == ["pr", "create"]:
        number = str(max((int(n) for n in state["prs"]), default=0) + 1)
        state["prs"][number] = {"title": argv[argv.index("--title") + 1], "branch": "new-branch",
                                "head_sha": "f" * 40, "files": [], "author": user}  # fmt: skip
        save(state)
        print(f"https://github.com/{state['repo']}/pull/{number}")
        return
    if argv[:2] == ["pr", "checkout"]:
        return
    if argv[0] == "api":
        args = argv[1:]
        method, etag, path, has_input = "GET", "", "", False
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--method":
                method = args[index + 1]
                index += 2
            elif arg == "-H":
                name, _, value = args[index + 1].partition(":")
                if name.strip().lower() == "if-none-match":
                    etag = value.strip()
                index += 2
            elif arg == "--hostname":
                index += 2
            elif arg == "--input":
                has_input = True
                index += 2
            elif arg == "--include":
                index += 1
            else:
                path = arg
                index += 1
        payload = json.loads(sys.stdin.read()) if has_input else None
        api(state, user, method, path, payload, etag)
    sys.stderr.write(f"fake gh: unhandled command {argv}\n")
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
