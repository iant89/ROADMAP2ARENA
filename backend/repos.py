"""Repository abstraction: one local git repository per owner.

A repo document (Mongo collection "repos") describes a local git repository on disk
and who owns it:

    {id, owner: {type: "job" | "project", id}, kind: "local", path (relative to the
     data dir), default_branch, head, commit_count, created_at, updated_at,
     remotes: [{provider: "github", full_name, html_url, clone_url, branch, pushed_head, pushed_at}]}

Jobs own private repositories (data/repos/<job_id>); project jobs are seeded from a
pinned commit in a separate imported project repository (data/projects/<project_id>).
A job document points at its repo with `repo_id`. Project and job repositories are
independent; generated commits never mutate the imported project repository.

All git work on one repo is serialised with a per-repo asyncio lock.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
import uuid

import git_cli
from orchestrator import now_iso

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
OWNER_TYPES = {"job": "repos", "project": "projects"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_locks: dict[str, asyncio.Lock] = {}
logger = logging.getLogger("roadmap2arena.repos")

# Obvious credential/config files should never be placed in an LLM prompt by default.
# This is a conservative filename filter, not a general secret scanner.
PROJECT_CONTEXT_EXCLUDES = [
    ".env", ".env.*", ".npmrc", ".pypirc", ".netrc", ".git-credentials",
    ".ssh/**", ".aws/**", ".azure/**", ".config/gcloud/**", ".docker/config.json",
    "id_rsa", "id_ed25519", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "*credentials*", "*.tfstate", "*.tfstate.*",
]


REPO_ROOT = os.path.dirname(BACKEND_DIR)
# Before F-006 the data lived in backend/data - inside uvicorn --reload's watch, so a job writing
# app/main.py into its repo restarted the backend and killed the running job.
LEGACY_DATA_DIR = os.path.join(BACKEND_DIR, "data")


def data_dir() -> str:
    """R2A_DATA_DIR, default <repo root>/data - deliberately outside backend/ (the reload watch)."""
    return os.path.abspath(os.environ.get("R2A_DATA_DIR") or os.path.join(REPO_ROOT, "data"))


def migrate_legacy_data(legacy_dir: str = LEGACY_DATA_DIR) -> dict:
    """Move job repos from the old backend/data/repos to <data_dir>/repos (once, at startup).

    Each repo directory is moved only if nothing exists at its new path (never overwrites); repo
    documents store paths relative to the data dir, so the DB needs no change. Empty legacy
    directories are removed afterwards. Returns {"moved": [...], "skipped": [...]} for logging/tests.
    """
    moved, skipped = [], []
    if (data_dir() + os.sep).startswith(BACKEND_DIR + os.sep):
        logger.warning("R2A_DATA_DIR %s is inside backend/ - with uvicorn --reload a job writing files "
                       "can restart the backend; use a directory outside backend/", data_dir())
    if os.environ.get("R2A_MIGRATE_LEGACY_DATA", "1").strip().lower() in ("0", "false", "no", "off"):
        return {"moved": moved, "skipped": skipped}  # test servers with throwaway data dirs
    old_root = os.path.abspath(legacy_dir)
    new_root = data_dir()
    if old_root == new_root or not os.path.isdir(os.path.join(old_root, "repos")):
        return {"moved": moved, "skipped": skipped}
    for kind in sorted(set(OWNER_TYPES.values())):
        src_dir, dst_dir = os.path.join(old_root, kind), os.path.join(new_root, kind)
        if not os.path.isdir(src_dir) or os.path.islink(src_dir):
            continue
        os.makedirs(dst_dir, exist_ok=True)
        for name in sorted(os.listdir(src_dir)):
            src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
            if os.path.lexists(dst):
                skipped.append(os.path.join(kind, name))
                logger.warning("Data migration: %s already exists - left %s in place", dst, src)
                continue
            shutil.move(src, dst)
            moved.append(os.path.join(kind, name))
        with contextlib.suppress(OSError):
            os.rmdir(src_dir)  # only if empty
    with contextlib.suppress(OSError):
        os.rmdir(old_root)
    if moved:
        logger.info("Data migration: moved %d repo(s) from %s to %s", len(moved), old_root, new_root)
    return {"moved": moved, "skipped": skipped}


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


def _flatten_shallow_history(root: str, head: str) -> str:
    """Turn a shallow checkout's missing-parent boundary into a complete local root commit.

    The upstream SHA is still recorded on the job, but its parents are intentionally not
    present after a depth-one import. Rewriting that boundary keeps job repositories
    pushable to a new GitHub repo and makes standalone bundles valid without downloading
    the source repository's potentially enormous history.
    """
    if git_cli.out(root, "rev-parse", "--is-shallow-repository").strip() != "true":
        return head
    commits = git_cli.out(root, "rev-list", "--reverse", "--topo-order", head).splitlines()
    if not commits:
        raise git_cli.GitError("could not read the imported project commit history")
    rewritten: dict[str, str] = {}
    for sha in commits:
        raw = git_cli.run(root, "cat-file", "commit", sha).stdout
        headers, separator, message = raw.partition(b"\n\n")
        if not separator:
            raise git_cli.GitError("could not parse an imported project commit")
        next_headers = []
        changed = False
        for line in headers.split(b"\n"):
            if line.startswith(b"parent "):
                parent = line[len(b"parent "):].decode("ascii", "replace")
                replacement = rewritten.get(parent)
                if replacement:
                    next_headers.append(b"parent " + replacement.encode("ascii"))
                    changed = changed or replacement != parent
                else:
                    # A parent beyond a shallow boundary is not available to this clone.
                    changed = True
            else:
                next_headers.append(line)
        if changed:
            # A changed parent invalidates a commit signature and merge tag.
            filtered, skip_continuation = [], False
            for line in next_headers:
                if skip_continuation and line.startswith(b" "):
                    continue
                skip_continuation = False
                if line.startswith((b"gpgsig ", b"gpgsig-sha256 ", b"mergetag ")):
                    skip_continuation = True
                    continue
                filtered.append(line)
            next_headers = filtered
            rewritten_raw = b"\n".join(next_headers) + separator + message
            rewritten[sha] = git_cli.out(root, "hash-object", "-t", "commit", "-w", "--stdin",
                                          input_bytes=rewritten_raw).strip()
        else:
            rewritten[sha] = sha
    flattened_head = rewritten.get(head)
    if not flattened_head:
        raise git_cli.GitError("could not preserve the imported project commit")
    git_cli.run(root, "update-ref", f"refs/heads/{git_cli.DEFAULT_BRANCH}", flattened_head)
    git_cli.run(root, "reset", "--hard", flattened_head)
    git_dir = git_cli.out(root, "rev-parse", "--absolute-git-dir").strip()
    with contextlib.suppress(FileNotFoundError):
        os.unlink(os.path.join(git_dir, "shallow"))
    if git_cli.out(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise git_cli.GitError("could not make the imported project history self-contained")
    return flattened_head


def _clone_at_sync(source_root: str, dest_root: str, commit: str) -> tuple[str, int, str]:
    """Create an independent, self-contained job repository at an immutable source commit.

    ``--no-hardlinks`` prevents future project refreshes or deletion from affecting the
    job repository. A shallow upstream boundary becomes a parentless local snapshot commit;
    the original source SHA is retained separately on the job. The imported project's
    origin is removed so generated jobs are never pushed implicitly to a source URL.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise ValueError("project commit must be a full lowercase Git SHA")
    os.makedirs(os.path.dirname(dest_root), exist_ok=True)
    git_cli.run(None, "clone", "--local", "--no-hardlinks", "--no-checkout", "--", source_root, dest_root,
                timeout=300)
    exists = git_cli.run(dest_root, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    if exists.returncode:
        raise git_cli.GitError("project snapshot is no longer available in the source repository")
    git_cli.run(dest_root, "checkout", "-q", "-B", git_cli.DEFAULT_BRANCH, commit, timeout=60)
    git_cli.run(dest_root, "remote", "remove", "origin", check=False)
    source_base = _flatten_shallow_history(dest_root, commit)
    head = git_cli.out(dest_root, "rev-parse", "HEAD").strip()
    count = int(git_cli.out(dest_root, "rev-list", "--count", "HEAD").strip())
    return head, count, source_base


async def create_job_repo_from_project(db, job_id: str, project_id: str, commit: str,
                                       source_job_id: str | None = None,
                                       project_base_commit: str | None = None) -> dict:
    """Seed a new job-owned repo from the selected project's pinned source commit.

    A restart may use its source job's private clone so it can still reproduce the
    original project snapshot after the project has been refreshed. Its local base SHA
    can differ from the upstream SHA when a shallow boundary was flattened. The project
    repo is the fallback source; each job owns an independent copy of the Git objects.
    """
    existing = await find(db, "job", job_id)
    if existing:
        return existing
    candidates = []
    if source_job_id:
        previous = await find(db, "job", source_job_id)
        if previous and exists_on_disk(previous):
            candidates.append((previous, project_base_commit or previous.get("project_base_commit") or commit))
    project_repo = await find(db, "project", project_id)
    if project_repo and exists_on_disk(project_repo):
        candidates.append((project_repo, commit))
    if not candidates:
        raise LookupError(f"Project {project_id} has no repository")

    rel = rel_path("job", job_id)
    target = os.path.abspath(os.path.join(data_dir(), rel))
    if not target.startswith(data_dir() + os.sep):
        raise ValueError("job repository path escapes the data dir")
    os.makedirs(data_dir(), exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".r2a-project-clone-", dir=data_dir())
    staged_repo = os.path.join(staging, "repo")
    moved = False
    doc = None
    try:
        last_error = None
        source_base_commit = None
        for source, source_sha in candidates:
            source_root = abs_path(source)
            try:
                async with lock_for(source_root):
                    head, count, source_base_commit = await asyncio.to_thread(
                        _clone_at_sync, source_root, staged_repo, source_sha)
                last_error = None
                break
            except git_cli.GitError as exc:
                last_error = exc
                shutil.rmtree(staged_repo, ignore_errors=True)
        if last_error is not None:
            raise last_error
        if os.path.lexists(target):
            raise FileExistsError(f"job repository already exists: {rel}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(staged_repo, target)
        moved = True
        ts = now_iso()
        doc = {"id": str(uuid.uuid4()), "owner": {"type": "job", "id": job_id}, "kind": "local",
               "path": rel, "default_branch": git_cli.DEFAULT_BRANCH, "head": head, "commit_count": count,
               "project_commit": commit, "project_base_commit": source_base_commit,
               "created_at": ts, "updated_at": ts, "remotes": []}
        await db.repos.insert_one(dict(doc))
        await db.jobs.update_one({"id": job_id}, {"$set": {"repo_id": doc["id"],
                                                               "project_base_commit": source_base_commit}})
        return doc
    except Exception:
        if doc:
            await db.repos.delete_one({"id": doc["id"]})
        if moved:
            shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def get_or_create(db, owner_type: str, owner_id: str) -> dict:
    """Return the owner's repo document, creating it (or a project-based job clone) if needed."""
    repo = await find(db, owner_type, owner_id)
    if repo is None and owner_type == "job":
        job = await db.jobs.find_one({"id": owner_id}, {"_id": 0, "project_id": 1, "project_commit": 1,
                                                        "project_base_commit": 1, "project_source_job_id": 1})
        if job and job.get("project_id") and job.get("project_commit"):
            return await create_job_repo_from_project(
                db, owner_id, job["project_id"], job["project_commit"], job.get("project_source_job_id"),
                job.get("project_base_commit"))
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
# Project jobs pass a bounded source snapshot to their first pending step. Snapshot
# reads committed content at the pinned ref, never the repository's working tree.
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
