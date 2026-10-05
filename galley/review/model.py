"""Review threads as the app shows them, built from GitHub's review comments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt

from galley.review import anchors

# Raw HTML in a comment is escaped, not rendered: comment bodies come from other people.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def render_markdown(text: str) -> str:
    return str(_MARKDOWN.render(text))


def initials(login: str) -> str:
    parts = [p for p in login.replace("_", "-").split("-") if p]
    letters = "".join(p[0] for p in parts[:2]) if len(parts) > 1 else login[:2]
    return letters.upper()


@dataclass
class Comment:
    id: int
    author: str
    avatar: str
    body: str
    created: str
    updated: str
    url: str
    mine: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "avatar": self.avatar,
            "body": self.body,
            "body_html": render_markdown(self.body),
            "created": self.created,
            "edited": self.updated != self.created,
            "url": self.url,
            "mine": self.mine,
        }


@dataclass
class Thread:
    """A conversation on one line. ``line`` is where it sits in the current text."""

    id: int  # id of the first comment
    path: str
    kind: str  # "line" (native), "anchored" (file-level with anchor), "file" (file-level)
    recorded_line: int | None
    quote: str
    node_id: str = ""
    resolved: bool = False
    line: int | None = None
    number: int = 0
    comments: list[Comment] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"c{self.id}"

    @property
    def outdated(self) -> bool:
        return self.kind != "file" and self.line is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ref": self.ref,
            "n": self.number,
            "path": self.path,
            "kind": self.kind,
            "line": self.line,
            "outdated": self.outdated,
            "resolved": self.resolved,
            "can_resolve": bool(self.node_id),
            "quote": self.quote,
            "initials": initials(self.comments[0].author) if self.comments else "",
            "comments": [c.as_dict() for c in self.comments],
        }


def build_threads(
    comments: list[dict[str, Any]], resolution: dict[int, dict[str, Any]], viewer: str
) -> list[Thread]:
    """Group GitHub review comments into threads (not yet located in any text)."""
    threads: dict[int, Thread] = {}
    for raw in sorted(comments, key=lambda c: (c.get("created_at", ""), c["id"])):
        anchor, body = anchors.parse_anchor(str(raw.get("body", "")))
        user = raw.get("user") or {}
        comment = Comment(
            id=int(raw["id"]),
            author=str(user.get("login", "")),
            avatar=str(user.get("avatar_url", "")),
            body=body,
            created=str(raw.get("created_at", "")),
            updated=str(raw.get("updated_at", "")),
            url=str(raw.get("html_url", "")),
            mine=str(user.get("login", "")) == viewer,
        )
        parent = raw.get("in_reply_to_id")
        if parent and int(parent) in threads:
            threads[int(parent)].comments.append(comment)
            continue
        if raw.get("subject_type") == "file":
            kind = "anchored" if anchor else "file"
            recorded, quote = (anchor.line, anchor.quote) if anchor else (None, "")
        else:
            kind = "line"
            recorded = raw.get("line") or raw.get("original_line")
            quote = anchors.hunk_quote(str(raw.get("diff_hunk", "")))
        state = resolution.get(comment.id, {})
        threads[comment.id] = Thread(
            id=comment.id,
            path=str(raw.get("path", "")),
            kind=kind,
            recorded_line=int(recorded) if recorded else None,
            quote=quote,
            node_id=str(state.get("id", "")),
            resolved=bool(state.get("resolved", False)),
            comments=[comment],
        )
    return list(threads.values())


def locate_threads(threads: list[Thread], texts: dict[str, str]) -> None:
    """Place each thread in the current text of its file and number them in reading order."""
    for thread in threads:
        text = texts.get(thread.path)
        if thread.kind == "file" or text is None:
            thread.line = None
            continue
        thread.line = anchors.locate(text.splitlines(), thread.quote, thread.recorded_line)
    ordered = sorted(threads, key=lambda t: (t.path, t.line is None, t.line or 0, t.id))
    for number, thread in enumerate(ordered, start=1):
        thread.number = number
