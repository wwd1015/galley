"""Thin wrapper around the ``gh`` CLI. All GitHub access goes through here.

Galley never stores a token: authentication is whatever ``gh auth`` holds.
GitHub Enterprise hosts are reached by passing ``hostname``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}
"""
RESOLVE_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
UNRESOLVE_MUTATION = """
mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
PAGE_SIZE = 100


class GhError(Exception):
    """``gh`` is missing, not authenticated, or the API refused the request."""


@dataclass(frozen=True)
class Response:
    status: int
    etag: str
    body: Any

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def parse_http(output: str) -> Response:
    """Split ``gh api --include`` output into status, ETag and JSON body."""
    head, _, body = output.replace("\r\n", "\n").partition("\n\n")
    lines = head.splitlines()
    status_line = re.match(r"HTTP/\S+\s+(\d{3})", lines[0]) if lines else None
    if status_line is None:
        raise GhError(f"unexpected output from gh api: {output[:200]!r}")
    etag = ""
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "etag":
            etag = value.strip()
    text = body.strip()
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = text
    return Response(status=int(status_line.group(1)), etag=etag, body=parsed)


class Gh:
    """One repository on one GitHub host, driven through the ``gh`` CLI."""

    def __init__(self, cwd: Path, hostname: str | None = None, executable: str = "gh") -> None:
        self.cwd = cwd
        self.hostname = hostname
        self.executable = executable
        self._repo: str | None = None
        self._viewer: str | None = None

    # -- plumbing ----------------------------------------------------------

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        if shutil.which(self.executable) is None:
            raise GhError("gh is not installed; see https://cli.github.com")
        env = dict(os.environ)
        if self.hostname:
            env["GH_HOST"] = self.hostname
        return subprocess.run(
            [self.executable, *args],
            cwd=self.cwd,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def _checked(self, args: list[str], stdin: str | None = None) -> str:
        result = self._run(args, stdin)
        if result.returncode != 0:
            raise GhError(result.stderr.strip() or f"gh {' '.join(args[:2])} failed")
        return result.stdout

    def api(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        etag: str = "",
    ) -> Response:
        """Call the REST API. With ``etag`` the request is conditional (a 304 is free)."""
        args = ["api", "--include", "--method", method, path]
        if self.hostname:
            args += ["--hostname", self.hostname]
        if etag:
            args += ["-H", f"If-None-Match: {etag}"]
        if payload is not None:
            args += ["--input", "-"]
        result = self._run(args, json.dumps(payload) if payload is not None else None)
        if not result.stdout.startswith("HTTP/"):
            raise GhError(result.stderr.strip() or "gh api failed")
        response = parse_http(result.stdout)
        if response.status >= 400:
            message = response.body.get("message") if isinstance(response.body, dict) else ""
            raise GhError(f"GitHub returned {response.status} for {method} {path}: {message}")
        return response

    def api_all(self, path: str) -> list[Any]:
        """Every page of a list endpoint."""
        items: list[Any] = []
        page = 1
        joiner = "&" if "?" in path else "?"
        while True:
            body = self.api(f"{path}{joiner}per_page={PAGE_SIZE}&page={page}").body
            if not isinstance(body, list):
                raise GhError(f"expected a list from {path}")
            items.extend(body)
            if len(body) < PAGE_SIZE:
                return items
            page += 1

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        args = ["api", "graphql", "--input", "-"]
        if self.hostname:
            args += ["--hostname", self.hostname]
        output = self._checked(args, json.dumps({"query": query, "variables": variables}))
        body = json.loads(output)
        if body.get("errors"):
            raise GhError("; ".join(str(e.get("message")) for e in body["errors"]))
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    # -- identity ----------------------------------------------------------

    def repo(self) -> str:
        """``owner/name`` of the repository in ``cwd``."""
        if self._repo is None:
            output = self._checked(["repo", "view", "--json", "nameWithOwner"])
            self._repo = str(json.loads(output)["nameWithOwner"])
        return self._repo

    def viewer(self) -> str:
        if self._viewer is None:
            self._viewer = str(self.api("user").body["login"])
        return self._viewer

    # -- pull requests -----------------------------------------------------

    def list_prs(self, suffix: str = ".qmd") -> list[dict[str, Any]]:
        """Open pull requests that touch a file ending in ``suffix``."""
        output = self._checked(
            ["pr", "list", "--state", "open", "--limit", "50", "--json",
             "number,title,headRefName,author,files,url"]
        )  # fmt: skip
        prs: list[dict[str, Any]] = []
        for pr in json.loads(output):
            paths = [f["path"] for f in pr.get("files") or []]
            if any(path.endswith(suffix) for path in paths):
                prs.append(
                    {
                        "number": int(pr["number"]),
                        "title": str(pr["title"]),
                        "branch": str(pr["headRefName"]),
                        "author": str((pr.get("author") or {}).get("login", "")),
                        "url": str(pr.get("url", "")),
                    }
                )
        return prs

    def pr(self, number: int, etag: str = "") -> Response:
        return self.api(f"repos/{self.repo()}/pulls/{number}", etag=etag)

    def pr_files(self, number: int) -> list[dict[str, Any]]:
        return self.api_all(f"repos/{self.repo()}/pulls/{number}/files")

    def create_pr(self, title: str, body: str = "") -> int:
        """Open a pull request from the current branch; return its number."""
        output = self._checked(["pr", "create", "--title", title, "--body", body])
        found = re.search(r"/pull/(\d+)", output)
        if not found:
            raise GhError(f"could not read the new pull request's number from: {output!r}")
        return int(found.group(1))

    def checkout(self, number: int) -> None:
        self._checked(["pr", "checkout", str(number)])

    # -- review comments ---------------------------------------------------

    def comments_changed(self, number: int, etag: str) -> Response:
        """Newest-first first page of review comments, conditional on ``etag``."""
        path = (
            f"repos/{self.repo()}/pulls/{number}/comments"
            f"?sort=updated&direction=desc&per_page={PAGE_SIZE}"
        )
        return self.api(path, etag=etag)

    def comments(self, number: int) -> list[dict[str, Any]]:
        return self.api_all(f"repos/{self.repo()}/pulls/{number}/comments")

    def review_threads(self, number: int) -> dict[int, dict[str, Any]]:
        """First-comment id -> ``{"id": thread node id, "resolved": bool}``."""
        owner, name = self.repo().split("/", 1)
        threads: dict[int, dict[str, Any]] = {}
        cursor: str | None = None
        while True:
            data = self.graphql(
                THREADS_QUERY, {"owner": owner, "name": name, "number": number, "cursor": cursor}
            )
            connection = data["repository"]["pullRequest"]["reviewThreads"]
            for node in connection["nodes"]:
                first = node["comments"]["nodes"]
                if first:
                    threads[int(first[0]["databaseId"])] = {
                        "id": str(node["id"]),
                        "resolved": bool(node["isResolved"]),
                    }
            if not connection["pageInfo"]["hasNextPage"]:
                return threads
            cursor = connection["pageInfo"]["endCursor"]

    def create_line_comment(
        self, number: int, commit_id: str, path: str, line: int, body: str
    ) -> dict[str, Any]:
        payload = {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        }
        response = self.api(
            f"repos/{self.repo()}/pulls/{number}/comments", method="POST", payload=payload
        )
        return dict(response.body)

    def create_file_comment(
        self, number: int, commit_id: str, path: str, body: str
    ) -> dict[str, Any]:
        payload = {"body": body, "commit_id": commit_id, "path": path, "subject_type": "file"}
        response = self.api(
            f"repos/{self.repo()}/pulls/{number}/comments", method="POST", payload=payload
        )
        return dict(response.body)

    def reply(self, number: int, comment_id: int, body: str) -> dict[str, Any]:
        response = self.api(
            f"repos/{self.repo()}/pulls/{number}/comments/{comment_id}/replies",
            method="POST",
            payload={"body": body},
        )
        return dict(response.body)

    def edit_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        response = self.api(
            f"repos/{self.repo()}/pulls/comments/{comment_id}",
            method="PATCH",
            payload={"body": body},
        )
        return dict(response.body)

    def delete_comment(self, comment_id: int) -> None:
        self.api(f"repos/{self.repo()}/pulls/comments/{comment_id}", method="DELETE")

    def set_resolved(self, thread_id: str, resolved: bool) -> None:
        self.graphql(RESOLVE_MUTATION if resolved else UNRESOLVE_MUTATION, {"id": thread_id})

    def submit_review(self, number: int, event: str, body: str = "") -> None:
        """``event`` is COMMENT, APPROVE or REQUEST_CHANGES."""
        if event not in ("COMMENT", "APPROVE", "REQUEST_CHANGES"):
            raise GhError(f"unknown review event '{event}'")
        self.api(
            f"repos/{self.repo()}/pulls/{number}/reviews",
            method="POST",
            payload={"event": event, "body": body},
        )
