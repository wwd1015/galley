"""Small helpers for the Pandoc JSON AST."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

Node = dict[str, Any]
INLINE_CONTAINERS = {
    "Emph", "Strong", "Underline", "Strikeout", "Superscript", "Subscript", "SmallCaps",
}  # fmt: skip


class PandocError(Exception):
    """Pandoc (run through Quarto) failed."""


def pandoc(args: list[str], cwd: Path, stdin: str | None = None) -> str:
    quarto = shutil.which("quarto")
    if quarto is None:
        raise PandocError("quarto not found on PATH")
    result = subprocess.run(
        [quarto, "pandoc", *args], cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise PandocError(result.stderr.strip() or "pandoc failed")
    return result.stdout


def str_(text: str) -> Node:
    return {"t": "Str", "c": text}


def space() -> Node:
    return {"t": "Space"}


def raw_inline(markdown: str) -> Node:
    return {"t": "RawInline", "c": ["markdown", markdown]}


def raw_block(markdown: str) -> Node:
    return {"t": "RawBlock", "c": ["markdown", markdown]}


def para(inlines: list[Node]) -> Node:
    return {"t": "Para", "c": inlines}


def text_inlines(text: str) -> list[Node]:
    out: list[Node] = []
    for index, word in enumerate(text.split()):
        if index:
            out.append(space())
        out.append(str_(word))
    return out


def stringify(node: Any) -> str:
    """Plain text of any AST fragment (inlines, blocks, meta values)."""
    if isinstance(node, list):
        return "".join(stringify(item) for item in node)
    if not isinstance(node, dict):
        return ""
    kind = node.get("t")
    content: Any = node.get("c")
    if kind == "Str":
        return str(content)
    if kind in ("Space", "SoftBreak", "LineBreak"):
        return " "
    if kind in ("Code", "Math", "RawInline"):
        return str(content[1])
    if kind in ("Span", "Link", "Image", "Quoted", "Cite"):
        return stringify(content[1])
    if kind == "Note":
        return ""
    if kind in ("Para", "Plain", "MetaInlines", "MetaBlocks", "BlockQuote", *INLINE_CONTAINERS):
        return stringify(content) + (" " if kind in ("Para", "Plain") else "")
    if kind == "MetaString":
        return str(content)
    if kind == "MetaList":
        return ", ".join(stringify(item).strip() for item in content)
    if kind == "Header":
        return stringify(content[2])
    if kind == "Div":
        return stringify(content[1])
    if kind in ("BulletList",):
        return " ".join(stringify(item) for item in content)
    if kind == "OrderedList":
        return " ".join(stringify(item) for item in content[1])
    return ""


def attribute(attr: list[Any], key: str) -> str | None:
    return next((value for name, value in attr[2] if name == key), None)


def map_inlines(inlines: list[Node], function: Callable[[list[Node]], list[Node]]) -> list[Node]:
    """Apply ``function`` to this inline list and, recursively, to every nested one."""
    out: list[Node] = []
    for node in inlines:
        kind = node.get("t")
        if kind in INLINE_CONTAINERS:
            node = {"t": kind, "c": map_inlines(node["c"], function)}
        elif kind in ("Span", "Link", "Quoted"):
            content = list(node["c"])
            content[1] = map_inlines(content[1], function)
            node = {"t": kind, "c": content}
        elif kind == "Note":
            node = {"t": kind, "c": map_blocks(node["c"], inline_function=function)}
        out.append(node)
    return function(out)


def map_blocks(
    blocks: list[Node],
    block_function: Callable[[list[Node]], list[Node]] | None = None,
    inline_function: Callable[[list[Node]], list[Node]] | None = None,
) -> list[Node]:
    """Rewrite block lists bottom-up, and the inline lists inside them."""
    out: list[Node] = []
    for node in blocks:
        kind = node.get("t")
        if kind in ("Para", "Plain") and inline_function:
            node = {"t": kind, "c": map_inlines(node["c"], inline_function)}
        elif kind == "Header" and inline_function:
            level, attr, inlines = node["c"]
            node = {"t": kind, "c": [level, attr, map_inlines(inlines, inline_function)]}
        elif kind == "BlockQuote":
            node = {"t": kind, "c": map_blocks(node["c"], block_function, inline_function)}
        elif kind == "Div":
            attr, inner = node["c"]
            node = {"t": kind, "c": [attr, map_blocks(inner, block_function, inline_function)]}
        elif kind == "BulletList":
            node = {
                "t": kind,
                "c": [map_blocks(item, block_function, inline_function) for item in node["c"]],
            }
        elif kind == "OrderedList":
            attrs, items = node["c"]
            node = {
                "t": kind,
                "c": [attrs, [map_blocks(i, block_function, inline_function) for i in items]],
            }
        out.append(node)
    return block_function(out) if block_function else out


def to_markdown(blocks: list[Node], api_version: list[int], cwd: Path) -> str:
    document = {"pandoc-api-version": api_version, "meta": {}, "blocks": blocks}
    return pandoc(
        ["--from=json", "--to=markdown-smart", "--wrap=none", "--markdown-headings=atx"],
        cwd,
        stdin=json.dumps(document),
    )
