"""Extract file artifacts from a model response.

The core matching (BLOCK_RE, PATH_COMMENT_RE, extract_artifacts) follows the
spec exactly: a fenced block is a named file when its info string is
"lang:path" (```python:src/main.py) or, failing that, when its first line is a
path comment ("# filename: x", "// file: x", "<!-- path: x"); the comment line
is dropped from the content. Other blocks are unnamed: they stay in the
transcript only and are never stored as artifacts or zipped.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

BLOCK_RE = re.compile(
    r"```(?:\w+)?(?::(?P<path1>[^\s`]+))?\s*\n"
    r"(?P<body>.*?)```",
    re.DOTALL,
)
PATH_COMMENT_RE = re.compile(
    r"^(?:#|//|<!--)\s*(?:file(?:name)?|path)\s*:\s*(?P<path2>[^\s>]+)"
)


def extract_artifacts(response: str) -> dict[str, str]:
    artifacts = {}
    for m in BLOCK_RE.finditer(response):
        path = m.group("path1")
        body = m.group("body")
        if not path:
            first_line = body.split("\n", 1)[0]
            cm = PATH_COMMENT_RE.match(first_line.strip())
            if cm:
                path = cm.group("path2")
                body = body.split("\n", 1)[1] if "\n" in body else ""
        if path:
            artifacts[path] = body.rstrip() + "\n"
    return artifacts


def count_unnamed_blocks(response: str) -> int:
    """Number of fenced blocks that extract_artifacts would ignore (for the job log)."""
    unnamed = 0
    for m in BLOCK_RE.finditer(response):
        if m.group("path1"):
            continue
        first_line = m.group("body").split("\n", 1)[0]
        if not PATH_COMMENT_RE.match(first_line.strip()):
            unnamed += 1
    return unnamed


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
