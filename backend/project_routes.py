"""Project workspaces: import a GitHub repository and use a pinned source snapshot for jobs.

A project owns an imported repository under ``R2A_DATA_DIR/projects/<id>``. Every project
job gets its own independent clone at submission time, so queued jobs keep the exact source
revision they were created against and never mutate the project workspace. The GitHub token
is supplied only to one git process through a host-scoped extra header; it is not persisted
in the remote URL or project document.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import git_cli
import github_client as ghc
import github_integration
import repos
import scheduler
from database import db as default_db
from github_client import GitHub, GitHubError, redact
from orchestrator import now_iso

router = APIRouter(prefix="/api")

PROJECT_NAME_MAX = 100
PROJECT_CONTEXT_MAX = 20_000
# Avoid unexpectedly importing multi-gigabyte working trees into the app's data directory.
MAX_REPOSITORY_SIZE_KB = 100_000
CLONE_TIMEOUT_SECONDS = 300


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_full_name: str = Field(..., min_length=3, max_length=200)
    name: str | None = Field(None, max_length=PROJECT_NAME_MAX)
    context: str = Field("", max_length=PROJECT_CONTEXT_MAX)
    branch: str | None = Field(None, max_length=200)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=PROJECT_NAME_MAX)
    context: str | None = Field(None, max_length=PROJECT_CONTEXT_MAX)


async def ensure_indexes(db) -> None:
    await db.projects.create_index([("id", 1)], unique=True)
    await db.projects.create_index([("updated_at", -1)])


def clean_project_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="Project name must not be empty")
    if len(cleaned) > PROJECT_NAME_MAX:
        raise HTTPException(status_code=422, detail=f"Project name must be at most {PROJECT_NAME_MAX} characters")
    return cleaned


def _validated_repository(data: dict, requested_full_name: str, branch_override: str | None = None) -> dict:
    """Validate GitHub metadata before using its clone URL or branch in a process."""
    full_name = str(data.get("full_name") or requested_full_name)
    if not ghc.FULL_NAME_RE.fullmatch(full_name):
        raise HTTPException(status_code=502, detail="GitHub returned an invalid repository name")
    if full_name.casefold() != requested_full_name.strip().casefold():
        raise HTTPException(status_code=502, detail="GitHub returned a different repository name")
    raw_clone_url = str(data.get("clone_url") or "")
    try:
        clone_url = github_integration.check_clone_url(raw_clone_url)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    parsed_url = urlparse(clone_url)
    if parsed_url.scheme == "https":
        expected_path = f"/{full_name}.git"
        if (parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment
                or parsed_url.port not in (None, 443) or unquote(parsed_url.path).lower() != expected_path.lower()):
            raise HTTPException(status_code=502, detail="GitHub returned an unsafe clone URL")
    raw_html_url = str(data.get("html_url") or "")
    html_url = raw_html_url or f"https://{parsed_url.hostname or 'github.com'}/{full_name}"
    html_parsed = urlparse(html_url)
    expected_html_path = f"/{full_name}"
    if (html_parsed.scheme != "https" or not html_parsed.hostname or html_parsed.username or html_parsed.password
            or html_parsed.query or html_parsed.fragment or html_parsed.port not in (None, 443)
            or unquote(html_parsed.path).rstrip("/").casefold() != expected_html_path.casefold()
            or (parsed_url.hostname and html_parsed.hostname.casefold() != parsed_url.hostname.casefold())):
        raise HTTPException(status_code=502, detail="GitHub returned an unsafe repository page URL")
    branch = (branch_override or data.get("default_branch") or "").strip()
    if not git_cli.valid_branch(branch):
        raise HTTPException(status_code=422, detail="Repository branch is missing or is not a valid branch name")
    raw_size = data.get("size", 0)
    try:
        size_kb = int(raw_size)
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="GitHub returned an invalid repository size") from None
    if size_kb < 0:
        raise HTTPException(status_code=502, detail="GitHub returned an invalid repository size")
    if size_kb > MAX_REPOSITORY_SIZE_KB:
        raise HTTPException(status_code=413, detail=f"Repository is too large to import (limit {MAX_REPOSITORY_SIZE_KB // 1000} MB)")
    return {"full_name": full_name, "clone_url": clone_url, "branch": branch, "size_kb": size_kb,
            "default_branch": data.get("default_branch") or branch, "private": bool(data.get("private")),
            "html_url": html_url, "description": data.get("description"),
            "remote_updated_at": data.get("updated_at")}


async def _github_repo(db, full_name: str) -> tuple[str, str, dict]:
    if not ghc.FULL_NAME_RE.fullmatch((full_name or "").strip()):
        raise HTTPException(status_code=422, detail="repo_full_name must look like owner/repository")
    full_name = full_name.strip()
    try:
        token = await github_integration._token_or_409(db)
        async with GitHub(token) as gh:
            response = await gh.request("GET", f"/repos/{full_name}", f"read repository {full_name}")
        return token, ((await ghc.get_doc(db)) or {}).get("username") or "x-access-token", response.json()
    except GitHubError as exc:
        raise github_integration._http_error(exc)


def _git_auth(clone_url: str, login: str, token: str) -> dict:
    parsed = urlparse(clone_url)
    host = (f"{parsed.scheme}://{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}/"
            if parsed.scheme in ("http", "https") and parsed.hostname else "https://github.com/")
    return github_integration.auth_env(login, token, host)


def _clone_remote_sync(clone_url: str, destination: str, branch: str, login: str, token: str) -> tuple[str, int]:
    """Shallow, single-branch import; no submodules, hooks, or URL credentials."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    git_cli.run(None, "clone", "--depth=1", "--single-branch", "--branch", branch,
                "--no-tags", "--no-recurse-submodules", "-c", "core.symlinks=false",
                "--", clone_url, destination, env=_git_auth(clone_url, login, token), timeout=CLONE_TIMEOUT_SECONDS)
    head = git_cli.run(destination, "rev-parse", "--verify", "-q", "HEAD", check=False)
    if head.returncode:
        raise git_cli.GitError("the selected repository branch has no commits")
    sha = head.stdout.decode().strip()
    count = int(git_cli.out(destination, "rev-list", "--count", "HEAD").strip())
    return sha, count


def _refresh_remote_sync(root: str, clone_url: str, branch: str, login: str, token: str) -> tuple[str, int]:
    """Update a managed project checkout to the selected remote branch (never force-pushes)."""
    dirty = git_cli.out(root, "status", "--porcelain").strip()
    if dirty:
        raise git_cli.GitError("project repository has local changes; refusing to replace them")
    git_cli.run(root, "remote", "set-url", "origin", clone_url)
    git_cli.run(root, "fetch", "--depth=1", "--no-tags", "origin", f"refs/heads/{branch}",
                env=_git_auth(clone_url, login, token), timeout=CLONE_TIMEOUT_SECONDS)
    git_cli.run(root, "checkout", "-q", "-B", git_cli.DEFAULT_BRANCH, "FETCH_HEAD", timeout=60)
    git_cli.run(root, "reset", "--hard", "FETCH_HEAD", timeout=60)
    head = git_cli.out(root, "rev-parse", "HEAD").strip()
    count = int(git_cli.out(root, "rev-list", "--count", "HEAD").strip())
    return head, count


def _project_view(project: dict, repo: dict | None = None, job_count: int | None = None) -> dict:
    source = project.get("source") or {}
    repo = repo or {}
    return {
        "id": project["id"], "name": project["name"], "context": project.get("context", ""),
        "source": {"provider": "github", "repo_full_name": source.get("repo_full_name"),
                   "html_url": source.get("html_url"), "private": bool(source.get("private")),
                   "branch": source.get("branch"), "default_branch": source.get("default_branch"),
                   "remote_updated_at": source.get("remote_updated_at"), "size_kb": source.get("size_kb", 0)},
        "repo_id": project.get("repo_id"), "head": project.get("head") or repo.get("head"),
        "commit_count": project.get("commit_count", repo.get("commit_count", 0)),
        "created_at": project.get("created_at"), "updated_at": project.get("updated_at"),
        "job_count": job_count,
    }


async def _project_or_404(db, project_id: str) -> dict:
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


async def _project_with_counts(db, project: dict) -> dict:
    repo = await repos.find(db, "project", project["id"])
    count = await db.jobs.count_documents({"project_id": project["id"]})
    return _project_view(project, repo, count)


@router.get("/projects")
async def list_projects():
    projects = await default_db.projects.find({}, {"_id": 0}).sort("updated_at", -1).limit(200).to_list(None)
    return [await _project_with_counts(default_db, p) for p in projects]


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate):
    full_name = body.repo_full_name.strip()
    token, login, metadata = await _github_repo(default_db, full_name)
    details = _validated_repository(metadata, full_name, body.branch)
    name = clean_project_name(body.name) or clean_project_name(str(metadata.get("name") or details["full_name"].split("/", 1)[1]))
    project_id = str(uuid.uuid4())
    rel = repos.rel_path("project", project_id)
    target = os.path.abspath(os.path.join(repos.data_dir(), rel))
    if not target.startswith(repos.data_dir() + os.sep):
        raise HTTPException(status_code=422, detail="Invalid project storage path")
    os.makedirs(repos.data_dir(), exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".r2a-project-import-", dir=repos.data_dir())
    staged_repo = os.path.join(staging, "repo")
    try:
        try:
            head, commit_count = await asyncio.to_thread(_clone_remote_sync, details["clone_url"], staged_repo,
                                                          details["branch"], login, token)
        except git_cli.GitError as exc:
            message = redact(str(exc), token)
            status = 413 if "no space" in message.lower() else 502
            raise HTTPException(status_code=status, detail=f"Could not import {details['full_name']}: {message}") from None
        if not head:
            raise HTTPException(status_code=422, detail="The selected repository branch has no commits")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise HTTPException(status_code=409, detail="Project storage already exists for this id")
        os.replace(staged_repo, target)
        ts = now_iso()
        repo = {"id": str(uuid.uuid4()), "owner": {"type": "project", "id": project_id}, "kind": "local",
                "path": rel, "default_branch": git_cli.DEFAULT_BRANCH, "head": head, "commit_count": commit_count,
                "created_at": ts, "updated_at": ts,
                "remotes": [{"provider": "github", "full_name": details["full_name"],
                             "html_url": details["html_url"], "clone_url": details["clone_url"],
                             "branch": details["branch"]}]}
        project = {"id": project_id, "name": name, "context": body.context.strip(), "repo_id": repo["id"],
                   "source": {"provider": "github", "repo_full_name": details["full_name"],
                              "html_url": details["html_url"], "private": details["private"],
                              "branch": details["branch"], "default_branch": details["default_branch"],
                              "remote_updated_at": details["remote_updated_at"], "size_kb": details["size_kb"]},
                   "head": head, "commit_count": commit_count, "created_at": ts, "updated_at": ts}
        try:
            await default_db.repos.insert_one(dict(repo))
            await default_db.projects.insert_one(dict(project))
        except Exception:
            await default_db.projects.delete_one({"id": project_id})
            await default_db.repos.delete_one({"id": repo["id"]})
            shutil.rmtree(target, ignore_errors=True)
            raise
        return _project_view(project, repo, 0)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    return await _project_with_counts(default_db, await _project_or_404(default_db, project_id))


@router.put("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate):
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one project field must be provided")
    changes = {}
    if "name" in body.model_fields_set:
        name = clean_project_name(body.name)
        if name is None:
            raise HTTPException(status_code=422, detail="Project name must not be empty")
        changes["name"] = name
    if "context" in body.model_fields_set:
        if body.context is None:
            raise HTTPException(status_code=422, detail="Project context must be a string")
        changes["context"] = body.context.strip()
    changes["updated_at"] = now_iso()
    await _project_or_404(default_db, project_id)
    await default_db.projects.update_one({"id": project_id}, {"$set": changes})
    return await _project_with_counts(default_db, await _project_or_404(default_db, project_id))


@router.post("/projects/{project_id}/refresh")
async def refresh_project(project_id: str):
    project = await _project_or_404(default_db, project_id)
    repo = await repos.find(default_db, "project", project_id)
    if not repo or not repos.exists_on_disk(repo):
        raise HTTPException(status_code=409, detail="Project repository is missing - re-import it from GitHub")
    source = project.get("source") or {}
    token, login, metadata = await _github_repo(default_db, source.get("repo_full_name", ""))
    details = _validated_repository(metadata, source.get("repo_full_name", ""), source.get("branch"))
    try:
        # Serialise against job creation/deletion while the project's default revision moves.
        # Existing project jobs already own independent clones and remain pinned to their SHA.
        async with scheduler.lock:
            project = await _project_or_404(default_db, project_id)
            repo = await repos.find(default_db, "project", project_id)
            if not repo or not repos.exists_on_disk(repo):
                raise HTTPException(status_code=409, detail="Project repository is missing - re-import it from GitHub")
            root = repos.abs_path(repo)
            async with repos.lock_for(root):
                head, commit_count = await asyncio.to_thread(_refresh_remote_sync, root, details["clone_url"],
                                                              details["branch"], login, token)
            ts = now_iso()
            await default_db.repos.update_one({"id": repo["id"]}, {"$set": {"head": head, "commit_count": commit_count,
                                                                               "updated_at": ts,
                                                                               "remotes.0.clone_url": details["clone_url"],
                                                                               "remotes.0.branch": details["branch"]}})
            await default_db.projects.update_one({"id": project_id}, {"$set": {
                "head": head, "commit_count": commit_count, "updated_at": ts,
                "source.branch": details["branch"], "source.default_branch": details["default_branch"],
                "source.remote_updated_at": details["remote_updated_at"], "source.size_kb": details["size_kb"],
            }})
    except git_cli.GitError as exc:
        message = redact(str(exc), token)
        raise HTTPException(status_code=502, detail=f"Could not refresh {details['full_name']}: {message}") from None
    return await _project_with_counts(default_db, await _project_or_404(default_db, project_id))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    async with scheduler.lock:
        project = await _project_or_404(default_db, project_id)
        jobs = await default_db.jobs.count_documents({"project_id": project_id})
        if jobs:
            raise HTTPException(status_code=409, detail=f"Project has {jobs} linked job(s); delete those jobs before deleting the project")
        await default_db.projects.delete_one({"id": project_id})
        await repos.remove(default_db, "project", project_id)
    return {"deleted": True, "project_id": project_id}
