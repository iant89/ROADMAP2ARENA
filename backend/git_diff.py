"""Structured diffs from git, for the shared DiffViewer (Git tab, step compare, repo browser).

diff_sync(root, base, head) runs `git diff -M` between two commits (base None = the empty tree,
i.e. everything added) and returns one entry per file with its unified patch, +/- counts and -
when small enough - the old and new file contents, so the viewer can expand collapsed unchanged
context and highlight syntax with full context. Paths are parsed from -z output (safe for any
file name). Sizes are capped; anything over a cap is flagged instead of sent.
"""
from __future__ import annotations

import git_cli

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_FILES = 300
MAX_PATCH_FILE = 200_000      # chars per file patch
MAX_PATCH_TOTAL = 2_000_000   # chars for all patches
MAX_CONTENT_FILE = 256_000    # bytes per old/new content
MAX_CONTENT_TOTAL = 3_000_000
STATUS_NAMES = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type_changed"}


def _name_status(root: str, base: str, head: str) -> list[dict]:
    raw = git_cli.out(root, "diff", "-z", "-M", "--name-status", "--no-ext-diff", base, head, "--")
    parts, files, i = raw.split("\0"), [], 0
    while i < len(parts) and parts[i]:
        code = parts[i][:1]
        if code in ("R", "C"):
            files.append({"status": STATUS_NAMES[code], "old_path": parts[i + 1], "path": parts[i + 2]})
            i += 3
        else:
            files.append({"status": STATUS_NAMES.get(code, code), "old_path": None, "path": parts[i + 1]})
            i += 2
    return files


def _numstat(root: str, base: str, head: str) -> list[tuple[int | None, int | None]]:
    """(additions, deletions) per file, in the same order as --name-status; None = binary."""
    raw = git_cli.out(root, "diff", "-z", "-M", "--numstat", "--no-ext-diff", base, head, "--")
    parts, out, i = raw.split("\0"), [], 0
    while i < len(parts) and parts[i]:
        fields = parts[i].split("\t")
        add, dele = fields[0], fields[1]
        out.append((int(add) if add.isdigit() else None, int(dele) if dele.isdigit() else None))
        i += 3 if len(fields) >= 3 and fields[2] == "" else 1  # renames: "a\td\t" NUL old NUL new
    return out


def _patches(root: str, base: str, head: str) -> list[str]:
    text = git_cli.out(root, "diff", "-M", "--no-color", "--no-ext-diff", "--patch", base, head, "--")
    chunks, cur = [], []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git ") and cur:
            chunks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def _blobs(root: str, specs: list[str]) -> list[bytes | None]:
    """Contents of `rev:path` specs via one `git cat-file --batch` (None if missing)."""
    if not specs:
        return []
    proc = git_cli.run(root, "cat-file", "--batch", input_bytes=("\n".join(specs) + "\n").encode(), timeout=60)
    data, pos, out = proc.stdout, 0, []
    for _ in specs:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].split()
        pos = nl + 1
        if len(header) == 2 and header[1] == b"missing":
            out.append(None)
            continue
        size = int(header[2])
        out.append(data[pos:pos + size])
        pos += size + 1
    return out


def resolve(root: str, ref: str) -> str | None:
    proc = git_cli.run(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def parent_of(root: str, sha: str) -> str | None:
    proc = git_cli.run(root, "rev-parse", "--verify", "-q", f"{sha}^1", check=False)
    return proc.stdout.decode().strip() if proc.returncode == 0 else None


def diff_sync(root: str, base: str | None, head: str, contents: bool = True) -> dict:
    """Structured diff base..head (commits already resolved; base None = empty tree)."""
    b = base or EMPTY_TREE
    files, stats, patches = _name_status(root, b, head), _numstat(root, b, head), _patches(root, b, head)
    truncated = len(files) > MAX_FILES
    files = files[:MAX_FILES]
    patch_total, specs, slots = 0, [], []
    for i, f in enumerate(files):
        add, dele = stats[i] if i < len(stats) else (None, None)
        patch = patches[i] if i < len(patches) else ""
        f.update(additions=add, deletions=dele, binary=add is None and dele is None and "Binary files" in patch,
                 patch=None, patch_too_large=False, old_content=None, new_content=None)
        if f["binary"]:
            continue
        if len(patch) > MAX_PATCH_FILE or patch_total + len(patch) > MAX_PATCH_TOTAL:
            f["patch_too_large"] = True
            truncated = truncated or patch_total + len(patch) > MAX_PATCH_TOTAL
            continue
        f["patch"] = patch
        patch_total += len(patch)
        if contents:
            if f["status"] != "added" and base:
                specs.append(f"{base}:{f['old_path'] or f['path']}")
                slots.append((i, "old_content"))
            if f["status"] != "deleted":
                specs.append(f"{head}:{f['path']}")
                slots.append((i, "new_content"))
    content_total = 0
    for (i, key), blob in zip(slots, _blobs(root, specs)):
        if blob is None or len(blob) > MAX_CONTENT_FILE or content_total + len(blob) > MAX_CONTENT_TOTAL or b"\0" in blob[:8000]:
            continue
        files[i][key] = blob.decode("utf-8", "replace")
        content_total += len(blob)
    for f in files:  # expanding context needs both sides; drop half-loaded pairs
        need_old = f["status"] != "added" and base
        need_new = f["status"] != "deleted"
        if (need_old and f["old_content"] is None) or (need_new and f["new_content"] is None):
            f["old_content"] = f["new_content"] = None
            f["context_expandable"] = False
        else:
            f["context_expandable"] = f["patch"] is not None
    return {"base": base, "head": head, "files": files, "truncated": truncated,
            "stats": {"files": len(files), "additions": sum(f["additions"] or 0 for f in files),
                      "deletions": sum(f["deletions"] or 0 for f in files)}}
