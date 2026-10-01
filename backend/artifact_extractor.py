"""Extract file artifacts from a model response.

A fenced block is a named file when its info string is "lang:path"
(```python:src/main.py) or, failing that, when its first line is a path
comment ("# filename: x", "// filename: x", "<!-- path: x -->"; the comment
line is dropped from the content). Other blocks are unnamed: they stay in the
transcript and are never stored as artifacts or zipped.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

BLOCK_RE = re.compile(r"^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
PATH_COMMENT_RE = re.compile(
    r"^\s*(?:#|//|<!--)\s*(?:filename|file|path)\s*:\s*(?P<path>\S.*?)\s*(?:-->)?\s*$",
    re.IGNORECASE,
)


def extract_blocks(response: str) -> list[dict]:
    """Return every fenced block as {lang, path|None, content}."""
    blocks = []
    for m in BLOCK_RE.finditer((response or "").replace("\r\n", "\n")):
        info = m.group(1).strip()
        body = m.group(2)
        if body.endswith("\n"):
            body = body[:-1]
        lang, _, path = info.partition(":")
        if path.strip():
            blocks.append({"lang": lang or "text", "path": path.strip(), "content": body})
            continue
        first, _, rest = body.partition("\n")
        c = PATH_COMMENT_RE.match(first)
        if c:
            blocks.append({"lang": info or "text", "path": c.group("path"), "content": rest})
        else:
            blocks.append({"lang": info or "text", "path": None, "content": body})
    return blocks


def extract_artifacts(response: str) -> tuple[list[dict], int]:
    """Return ([{path, content}], unnamed_count). Last block wins within a response."""
    blocks = extract_blocks(response)
    named: dict[str, str] = {}
    for b in blocks:
        if b["path"]:
            named[b["path"]] = b["content"]
    unnamed = sum(1 for b in blocks if not b["path"])
    return [{"path": p, "content": c} for p, c in named.items()], unnamed


def clean_zip_path(raw: str) -> tuple[str | None, str | None]:
    """Normalise a path for the ZIP. Returns (path, None) or (None, skip_reason)."""
    p = str(raw or "").strip().replace("\\", "/")
    prev = None
    while p != prev:
        prev = p
        p = p.lstrip("/")
        while p.startswith("./"):
            p = p[2:]
    if p.endswith("/"):
        return None, "empty file name"
    segments = [s for s in p.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segments):
        return None, 'contains a ".." segment'
    if not segments:
        return None, "empty file name"
    return str(PurePosixPath(*segments)), None
