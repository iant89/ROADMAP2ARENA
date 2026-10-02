"""Repository abstraction: one local git repository per owner.

A repo document (Mongo collection "repos") describes a local git repository on disk
and who owns it:

    {id, owner: {type: "job" | "project", id}, kind: "local", path (relative to the
     data dir), default_branch, head, commit_count, created_at, updated_at,
     remotes: [{provider: "github", full_name, html_url, clone_url, branch, pushed_head, pushed_at}]}

Today only jobs own repos (data/repos/<job_id>); a job document points at its repo
with `repo_id`. Projects (planned) will own their own repo (data/projects/<id>) and
receive the commits of their jobs, so nothing here assumes the owner is a job.

All git work on one repo is serialised with a per-repo asyncio lock.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid

import git_cli
from orchestrator import now_iso

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
OWNER_TYPES = {"job": "repos", "project": "projects"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_locks: dict[str, asyncio.Lock] = {}


def data_dir() -> str:
    return os.path.abspath(os.environ.get("R2A_DATA_DIR") or os.path.join(BACKEND_DIR, "data"))


def rel_path(owner_type: str, owner_id: str) -> str:
    if owner_type not in OWNER_TYPES or not _ID_RE.match(owner_id or ""):
        raise ValueError(f"invalid repo owner {owner_type}:{owner_id!r}")
    return os.path.join(OWNER_TYPES[owner_type], owner_id)


def abs_path(repo: dict) -> str:
    path = os.path.abspath(os.path.join(data_dir(), repo["path"]))
    if not path.startswith(data_dir() + os.sep):
        raise ValueError("repo path escapes the data dir")
    return path


def lock_for(repo_path: str) -> asyncio.Lock:
    return _locks.setdefault(repo_path, asyncio.Lock())


async def ensure_indexes(db) -> None:
    await db.repos.create_index([("id", 1)], unique=True)
    await db.repos.create_index([("owner.type", 1), ("owner.id", 1)], unique=True)


async def find(db, owner_type: str, owner_id: str) -> dict | None:
    return await db.repos.find_one({"owner.type": owner_type, "owner.id": owner_id}, {"_id": 0})


def _init_on_disk(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        git_cli.run(None, "init", "-q", "-b", git_cli.DEFAULT_BRANCH, "--", path)


async def get_or_create(db, owner_type: str, owner_id: str) -> dict:
    """Return the owner's repo document, creating the repo (doc + `git init`) if needed."""
    repo = await find(db, owner_type, owner_id)
    if repo is None:
        ts = now_iso()
        repo = {"id": str(uuid.uuid4()), "owner": {"type": owner_type, "id": owner_id}, "kind": "local",
                "path": rel_path(owner_type, owner_id), "default_branch": git_cli.DEFAULT_BRANCH,
                "head": None, "commit_count": 0, "created_at": ts, "updated_at": ts, "remotes": []}
        try:
            await db.repos.insert_one(dict(repo))
        except Exception:  # noqa: BLE001 - lost a creation race: use the winner
            repo = await find(db, owner_type, owner_id)
        if owner_type == "job":
            await db.jobs.update_one({"id": owner_id}, {"$set": {"repo_id": repo["id"]}})
    await asyncio.to_thread(_init_on_disk, abs_path(repo))
    return repo


def exists_on_disk(repo: dict) -> bool:
    return os.path.isdir(os.path.join(abs_path(repo), ".git"))


async def remove(db, owner_type: str, owner_id: str) -> bool:
    """Delete the owner's repo document and its directory. Returns True if something was removed."""
    repo = await find(db, owner_type, owner_id)
    try:
        path = abs_path(repo) if repo else os.path.join(data_dir(), rel_path(owner_type, owner_id))
    except ValueError:
        return False
    async with lock_for(path):
        existed = os.path.exists(path)
        if existed:
            await asyncio.to_thread(shutil.rmtree, path, True)
        if repo:
            await db.repos.delete_one({"id": repo["id"]})
    return existed or bool(repo)


# ------------------------------------------------------------------ snapshots (read-only)
# Used to show a model the code in a repo (planned: project jobs start from a snapshot of
# the project repo). Reads committed content at a ref, never the working tree.
def _match(path: str, patterns: list[str]) -> bool:
    import fnmatch
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        # "dir/" or "dir/**" match everything below dir; plain names match at any depth.
        if pat.endswith("/"):
            pat += "**"
        if fnmatch.fnmatchcase(path, pat) or ("/" not in pat and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pat)):
            return True
        if pat.endswith("/**") and (path + "/").startswith(pat[:-2]):
            return True
    return False


def read_tree_sync(root: str, ref: str = "HEAD") -> list[dict]:
    """Files at `ref`: [{path, size, sha}] sorted by path ([] for an empty repo)."""
    proc = git_cli.run(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    if proc.returncode != 0:
        return []
    commit = proc.stdout.decode().strip()
    raw = git_cli.out(root, "ls-tree", "-r", "-l", "-z", "--full-tree", commit)
    files = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, kind, sha, size = meta.split()
        if kind == "blob" and mode != "120000":  # skip symlinks and submodules
            files.append({"path": path, "size": int(size) if size.isdigit() else 0, "sha": sha})
    return sorted(files, key=lambda f: f["path"])


def read_blobs_sync(root: str, shas: list[str]) -> dict[str, bytes]:
    """Contents of several blobs in one `git cat-file --batch` call."""
    if not shas:
        return {}
    proc = git_cli.run(root, "cat-file", "--batch", input_bytes=("\n".join(shas) + "\n").encode())
    data, pos, outd = proc.stdout, 0, {}
    for sha in shas:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode().split()
        pos = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        outd[sha] = data[pos:pos + size]
        pos += size + 1
    return outd


def snapshot_sync(root: str, ref: str = "HEAD", include: list[str] | None = None,
                  exclude: list[str] | None = None, budget_bytes: int = 200_000,
                  max_file_bytes: int = 50_000) -> dict:
    """Tree + file contents at `ref` within a byte budget.

    include: globs a file must match (empty = all); exclude: globs that drop a file.
    Text files are added in path order until the budget is used; files over max_file_bytes, binary files and files past the budget are listed in the
    tree but their content is omitted (with a reason).
    Returns {ref, commit, tree: [{path, size, included, reason}], files: [{path, content}],
             used_bytes, budget_bytes, truncated}.
    """
    include, exclude = include or [], exclude or []
    tree = read_tree_sync(root, ref)
    commit = git_cli.out(root, "rev-parse", f"{ref}^{{commit}}").strip() if tree else None
    selected = [f for f in tree if (not include or _match(f["path"], include)) and not _match(f["path"], exclude)]
    candidates = [f for f in selected if f["size"] <= max_file_bytes]
    blobs = read_blobs_sync(root, [f["sha"] for f in candidates])
    used, files, info = 0, [], {}
    for f in selected:
        if f["size"] > max_file_bytes:
            info[f["path"]] = "too large"
            continue
        raw = blobs.get(f["sha"], b"")
        if b"\0" in raw[:8000]:
            info[f["path"]] = "binary"
            continue
        if used + len(raw) > budget_bytes:
            info[f["path"]] = "over budget"
            continue
        used += len(raw)
        files.append({"path": f["path"], "content": raw.decode("utf-8", "replace")})
        info[f["path"]] = None
    out_tree = [{"path": f["path"], "size": f["size"],
                 "included": info.get(f["path"], "excluded") is None,
                 "reason": info.get(f["path"], "excluded")} for f in tree]
    return {"ref": ref, "commit": commit, "tree": out_tree, "files": files, "used_bytes": used,
            "budget_bytes": budget_bytes, "truncated": any(r in ("too large", "over budget") for r in info.values())}


async def snapshot(repo: dict, ref: str = "HEAD", **kw) -> dict:
    root = abs_path(repo)
    async with lock_for(root):
        return await asyncio.to_thread(snapshot_sync, root, ref, **kw)
