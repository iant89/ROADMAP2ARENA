"""Parse a ROADMAP.md into ordered steps.

Rules (kept identical to frontend/src/lib/roadmapParser.js):
1. "### <title>" starts a step; the body until the next "###" (or EOF) is the
   description. A "###" inside a fenced code block does not start a step.
2. "- [ ] <title>" outside a ### block is a standalone step (empty description).
3. Anything else outside a ### block is ignored (other headings, paragraphs,
   checked boxes "- [x]").
"""
from __future__ import annotations

import re

STEP_HEADING = re.compile(r"^###\s+(.+?)\s*#*\s*$")
UNCHECKED_ITEM = re.compile(r"^\s*[-*+]\s+\[ \]\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)


def parse_roadmap(markdown: str) -> list[dict]:
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    steps: list[dict] = []
    current: dict | None = None
    in_fence = False

    def close() -> None:
        nonlocal current
        if current is not None:
            steps.append({"title": current["title"], "description": "\n".join(current["body"]).strip()})
            current = None

    for line in text.split("\n"):
        if current is not None and FENCE.match(line):
            in_fence = not in_fence
            current["body"].append(line)
            continue
        if not in_fence:
            heading = STEP_HEADING.match(line)
            if heading:
                close()
                current = {"title": heading.group(1).strip(), "body": []}
                continue
        if current is not None:
            current["body"].append(line)
            continue
        item = UNCHECKED_ITEM.match(line)
        if item:
            steps.append({"title": item.group(1).strip(), "description": ""})
    close()
    return [{"index": i + 1, **s} for i, s in enumerate(steps)]


def roadmap_title(markdown: str) -> str | None:
    m = H1.search(markdown or "")
    return m.group(1).strip() if m else None
