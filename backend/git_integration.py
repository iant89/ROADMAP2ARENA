"""Per-job git history: every completed step is committed to the job's local repo.

- After a step completes, its artifacts are written into the job's working tree (files
  accumulate across steps, like the ZIP) and committed as "Step N: <title>" by
  ROADMAP2ARENA. The commit sha is stored on the step (`commit_sha`).
- `sync_job` commits every done step that has no commit yet, in order. It runs at the
  start of every run (so a resume continues the same repo and old jobs catch up), after
  each step, and from POST /api/jobs/{id}/git/init (create a repo for an older job).
- Restart and clone create new jobs, so they get a new repo. Deleting a job removes its
  repo (deletion_routes.on_delete).
- Git problems are logged to the job log and never fail the job.

Routes (prefix /api/jobs/{id}/git): GET "" (repo summary + commits), POST /init,
GET /commits/{sha} (files + patch + structured diff), GET /compare?base=&head= (structured diff,
see git_diff.py), GET /download?format=zip|bundle.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile
import zipfile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

import git_cli
import git_diff
import repos
from artifact_extractor import clean_zip_path
from database import db as default_db
from orchestrator import append_log, now_iso

router = APIRouter(prefix="/api")
logger = logging.getLogger("roadmap2arena.git")

LOG_MAX = 500
PATCH_MAX_CHARS = 400_000
STEP_SUBJECT_RE = re.compile(r"^Step (\d+): ")
SHORTSTAT_RE = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


# ------------------------------------------------------------------ committing
def safe_rel_path(raw: str) -> tuple[str | None, str | None]:
    """Normalised relative path for the working tree, or (None, reason)."""
    path, reason = clean_zip_path(raw)
    if reason:
        return None, reason
    if any(seg.lower() == ".git" for seg in path.split("/")):
        return None, 'contains a ".git" segment'
    return path, None


def _write_artifact(root: str, rel: str, content: str) -> str | None:
    """Write one file below root. Returns a skip reason or None."""
    target = os.path.join(root, *rel.split("/"))
    real_root = os.path.realpath(root)
    parent = os.path.dirname(target)
    # Walk the parents: they must be real directories (or missing) inside the root.
    cur = root
    for seg in rel.split("/")[:-1]:
        cur = os.path.join(cur, seg)
        if os.path.islink(cur):
            return f"parent {os.path.relpath(cur, root)} is a symlink"
        if os.path.exists(cur) and not os.path.isdir(cur):
            return f"conflicts with the file {os.path.relpath(cur, root)}"
    if os.path.isdir(target) and not os.path.islink(target):
        return "a directory with that name already exists"
    os.makedirs(parent, exist_ok=True)
    if not os.path.realpath(parent).startswith(real_root):
        return "resolves outside the repository"
    if os.path.islink(target):
        os.unlink(target)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return None


def one_line(text: str, limit: int = 200) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _commit_step_sync(root: str, job_id: str, step: dict, step_total: int) -> dict:
    written, skipped = [], []
    for a in step.get("artifacts", []):
        rel, reason = safe_rel_path(a.get("path", ""))
        if not reason:
            try:
                reason = _write_artifact(root, rel, a.get("content", ""))
            except OSError as exc:
                reason = f"could not write ({exc.strerror or exc})"
        if reason:
            skipped.append({"path": a.get("path", ""), "reason": reason})
        else:
            written.append(rel)
    git_cli.run(root, "add", "-A")
    idx = step["index"]
    body = (f"Step {idx}: {one_line(step.get('title')) or 'Untitled step'}\n\n"
            f"{len(written)} file{'s' if len(written) != 1 else ''} from step {idx}/{step_total} of "
            f"ROADMAP2ARENA job {job_id}.\n\nR2A-Job: {job_id}\nR2A-Step: {idx}\n")
    git_cli.run(root, "commit", "-q", "--allow-empty", "--no-verify", "-F", "-", input_bytes=body.encode())
    sha = git_cli.out(root, "rev-parse", "HEAD").strip()
    return {"sha": sha, "written": written, "skipped": skipped}


def _head_sync(root: str) -> tuple[str | None, int]:
    proc = git_cli.run(root, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if proc.returncode != 0:
        return None, 0
    return proc.stdout.decode().strip(), int(git_cli.out(root, "rev-list", "--count", "HEAD").strip())


async def sync_job(db, job_id: str, reason: str = "") -> dict:
    """Commit every done step of the job that has no commit yet (in step order).

    Returns {"repo": repo_doc, "committed": [indexes]}. Raises GitError/OSError on failure.
    """
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "id": 1, "step_total": 1})
    if not job:
        raise LookupError(job_id)
    existed = await repos.find(db, "job", job_id)
    repo = await repos.get_or_create(db, "job", job_id)
    root = repos.abs_path(repo)
    committed = []
    async with repos.lock_for(root):
        head, _ = await asyncio.to_thread(_head_sync, root)
        query = {"job_id": job_id, "status": "done"}
        if head:  # repo has history: only steps without a commit
            query["commit_sha"] = {"$in": [None, ""]}
        steps = await db.steps.find(query, {"_id": 0, "index": 1, "title": 1, "artifacts": 1}).sort("index", 1).to_list(None)
        if not existed:
            await append_log(db, job_id, "info", f"Git: new repository {repo['path']}{reason}")
        for step in steps:
            res = await asyncio.to_thread(_commit_step_sync, root, job_id, step, job["step_total"])
            await db.steps.update_one({"job_id": job_id, "index": step["index"]}, {"$set": {"commit_sha": res["sha"]}})
            committed.append(step["index"])
            n = len(res["written"])
            await append_log(db, job_id, "ok", f"Git: committed step {step['index']} ({res['sha'][:7]}, "
                                               f"{n} file{'s' if n != 1 else ''})")
            for s in res["skipped"]:
                await append_log(db, job_id, "warn", f'Git: skipped "{s["path"]}" - {s["reason"]}')
        head, count = await asyncio.to_thread(_head_sync, root)
        await db.repos.update_one({"id": repo["id"]}, {"$set": {"head": head, "commit_count": count, "updated_at": now_iso()}})
    repo.update(head=head, commit_count=count)
    return {"repo": repo, "committed": committed}


async def safe_sync(db, job_id: str, reason: str = "") -> None:
    """sync_job for orchestrator hooks: never raises (a git problem must not break a job)."""
    try:
        await sync_job(db, job_id, reason)
    except LookupError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("git sync failed for job %s", job_id)
        try:
            await append_log(db, job_id, "warn", f"Git: commit failed - {exc}")
        except Exception:  # noqa: BLE001
            pass


async def on_run_start(db, job_id: str) -> None:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "restarted_from": 1, "cloned_from": 1})
    reason = ""
    if job and job.get("restarted_from"):
        reason = f" (restart of {job['restarted_from'][:8]} - new history)"
    elif job and job.get("cloned_from"):
        reason = f" (clone of {job['cloned_from'][:8]} - new history)"
    await safe_sync(db, job_id, reason)


async def on_step_done(db, job_id: str, _index: int) -> None:
    await safe_sync(db, job_id)


async def on_delete(db, job_id: str) -> None:
    await repos.remove(db, "job", job_id)


# ------------------------------------------------------------------ reading
def _log_sync(root: str, limit: int) -> list[dict]:
    raw = git_cli.out(root, "log", f"-n{limit}", "--format=%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s", "--shortstat")
    commits = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        head, _, rest = rec.partition("\n")
        sha, short, an, ae, date, subject = head.split("\x1f", 5)
        m = SHORTSTAT_RE.search(rest)
        sm = STEP_SUBJECT_RE.match(subject)
        commits.append({"sha": sha, "short_sha": short, "author_name": an, "author_email": ae, "date": date,
                        "message": subject, "step_index": int(sm.group(1)) if sm else None,
                        "files_changed": int(m.group(1)) if m else 0,
                        "insertions": int(m.group(2) or 0) if m else 0,
                        "deletions": int(m.group(3) or 0) if m else 0})
    return commits


STATUS_NAMES = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type_changed"}


def _commit_sync(root: str, sha: str) -> dict | None:
    proc = git_cli.run(root, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}", check=False)
    if proc.returncode != 0:
        return None
    full = proc.stdout.decode().strip()
    meta = git_cli.out(root, "show", "-s", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%P%x1f%B", full)
    h, short, an, ae, date, parents, body = meta.split("\x1f", 6)
    numstat = git_cli.out(root, "diff-tree", "--root", "-r", "--no-commit-id", "-M", "--numstat", full)
    names = git_cli.out(root, "diff-tree", "--root", "-r", "--no-commit-id", "-M", "--name-status", full)
    stats = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add, dele = parts[0], parts[1]
            stats[parts[-1]] = (int(add) if add.isdigit() else None, int(dele) if dele.isdigit() else None)
    files = []
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, path = parts[0][:1], parts[-1]
        add, dele = stats.get(path, (None, None))
        files.append({"path": path, "status": STATUS_NAMES.get(code, code), "old_path": parts[1] if len(parts) == 3 else None,
                      "additions": add, "deletions": dele})
    diff = git_diff.diff_sync(root, git_diff.parent_of(root, full), full)
    patch = git_cli.out(root, "show", "--format=", "--patch", "--no-color", "--no-ext-diff", "-M", full)
    truncated = len(patch) > PATCH_MAX_CHARS
    sm = STEP_SUBJECT_RE.match(body)
    return {"sha": h, "short_sha": short, "author_name": an, "author_email": ae, "date": date,
            "parents": parents.split(), "message": body.strip(), "subject": body.split("\n", 1)[0],
            "step_index": int(sm.group(1)) if sm else None, "files": files,
            "patch": patch[:PATCH_MAX_CHARS], "patch_truncated": truncated, "diff": diff}


async def _job_or_404(db, job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "id": 1, "status": 1, "steps_done": 1, "title": 1,
                                                  "project_id": 1, "repo_id": 1})
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


async def _repo_or_404(db, job_id: str) -> tuple[dict, str]:
    repo = await repos.find(db, "job", job_id)
    if not repo or not repos.exists_on_disk(repo):
        raise HTTPException(status_code=404, detail="This job has no git repository yet")
    return repo, repos.abs_path(repo)


async def summary(db, job_id: str, limit: int = LOG_MAX) -> dict:
    job = await _job_or_404(db, job_id)
    unsynced = await db.steps.count_documents({"job_id": job_id, "status": "done", "commit_sha": {"$in": [None, ""]}})
    base = {"job_id": job_id, "project_id": job.get("project_id"), "job_status": job["status"],
            "uncommitted_steps": unsynced}
    repo = await repos.find(db, "job", job_id)
    if not repo or not repos.exists_on_disk(repo):
        return {**base, "exists": False, "repo_id": None, "commits": [], "commit_count": 0, "head": None,
                "can_init": True}
    root = repos.abs_path(repo)
    async with repos.lock_for(root):
        head, count = await asyncio.to_thread(_head_sync, root)
        commits = await asyncio.to_thread(_log_sync, root, limit) if head else []
    return {**base, "exists": True, "can_init": False, "repo_id": repo["id"], "owner": repo["owner"],
            "path": repo["path"], "default_branch": repo.get("default_branch", git_cli.DEFAULT_BRANCH),
            "head": head, "commit_count": count, "commits": commits, "remotes": repo.get("remotes", [])}


# ------------------------------------------------------------------ routes
@router.get("/jobs/{job_id}/git")
async def get_git(job_id: str, limit: int = Query(LOG_MAX, ge=1, le=LOG_MAX)):
    return await summary(default_db, job_id, limit)


@router.post("/jobs/{job_id}/git/init")
async def init_git(job_id: str):
    """Create the repo now and commit all done steps that have no commit (e.g. older jobs)."""
    await _job_or_404(default_db, job_id)
    try:
        res = await sync_job(default_db, job_id, " (created from the job history)")
    except git_cli.GitError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {**await summary(default_db, job_id), "committed_steps": res["committed"]}


@router.get("/jobs/{job_id}/git/commits/{sha}")
async def get_commit(job_id: str, sha: str):
    await _job_or_404(default_db, job_id)
    if not git_cli.SHA_RE.match(sha):
        raise HTTPException(status_code=422, detail="sha must be 4-40 lowercase hex characters")
    _, root = await _repo_or_404(default_db, job_id)
    async with repos.lock_for(root):
        commit = await asyncio.to_thread(_commit_sync, root, sha)
    if not commit:
        raise HTTPException(status_code=404, detail=f"Commit {sha} not found in this job's repository")
    return commit


def _compare_sync(root: str, base: str | None, head: str) -> dict | None:
    h = git_diff.resolve(root, head)
    if not h:
        return None
    b = git_diff.resolve(root, base) if base else git_diff.parent_of(root, h)
    if base and not b:
        return None
    return git_diff.diff_sync(root, b, h)


@router.get("/jobs/{job_id}/git/compare")
async def compare(job_id: str, head: str = Query(...), base: str | None = Query(None)):
    """Structured diff base..head for the DiffViewer (base defaults to head's parent / the empty tree).
    Compare two steps by passing their commit_sha values."""
    await _job_or_404(default_db, job_id)
    for name, value in (("head", head), ("base", base)):
        if value is not None and not git_cli.SHA_RE.match(value):
            raise HTTPException(status_code=422, detail=f"{name} must be 4-40 lowercase hex characters")
    _, root = await _repo_or_404(default_db, job_id)
    async with repos.lock_for(root):
        result = await asyncio.to_thread(_compare_sync, root, base, head)
    if result is None:
        raise HTTPException(status_code=404, detail="Commit not found in this job's repository")
    return result


def _zip_repo(root: str, prefix: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                zf.write(full, f"{prefix}/{os.path.relpath(full, root)}")
            for d in dirnames:  # keep empty directories git needs (refs/tags, objects/info, ...)
                full = os.path.join(dirpath, d)
                if not os.listdir(full):
                    zf.writestr(f"{prefix}/{os.path.relpath(full, root)}/", "")
    return buf.getvalue()


def _bundle_repo(root: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="r2a-bundle-") as tmp:
        path = os.path.join(tmp, "repo.bundle")
        git_cli.run(root, "bundle", "create", "-q", path, "--all")
        with open(path, "rb") as fh:
            return fh.read()


@router.get("/jobs/{job_id}/git/download")
async def download_repo(job_id: str, format: str = Query("zip", pattern="^(zip|bundle)$")):  # noqa: A002
    await _job_or_404(default_db, job_id)
    _, root = await _repo_or_404(default_db, job_id)
    name = f"roadmap2arena-{job_id[:8]}"
    async with repos.lock_for(root):
        head, count = await asyncio.to_thread(_head_sync, root)
        if not head:
            raise HTTPException(status_code=409, detail="The repository has no commits yet - nothing to download")
        if format == "bundle":
            data, media, filename = await asyncio.to_thread(_bundle_repo, root), "application/octet-stream", f"{name}.bundle"
        else:
            data, media, filename = await asyncio.to_thread(_zip_repo, root, name), "application/zip", f"{name}-repo.zip"
    await append_log(default_db, job_id, "ok", f"Git: downloaded repository as {format} ({count} commits)")
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
