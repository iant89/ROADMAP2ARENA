"""ROADMAP2ARENA backend: FastAPI app, all routes under /api."""
from __future__ import annotations

import asyncio

import io
import logging
import uuid
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING

import app_settings
import orchestrator
import providers
import scheduler
import settings
from artifact_extractor import clean_zip_path
from database import db, mongo
from orchestrator import append_log, now_iso
from deletion_routes import router as deletion_router
from history_routes import router as history_router
from notification_routes import router as notification_router
import notifier
import deletion_routes
import git_integration
import repos
from git_integration import router as git_router
import github_integration
from github_integration import router as github_router
from queue_routes import queue_state, router as queue_router
from provider_routes import router as provider_router
from roadmap_parser import parse_roadmap, roadmap_title

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("roadmap2arena")

INTERRUPTED = "interrupted by server restart"
# Upper bound for step indexes in URLs; keeps values inside Mongo's 64-bit int range.
MAX_STEP_INDEX = 100_000
FINISHED = ("done", "error", "stopped", "cancelled")
RESUMABLE = ("error", "stopped", "cancelled")
ALL_STATUSES = ("queued", "paused", "running", "done", "error", "stopped", "cancelled")
LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX = 20, 200


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.jobs.create_index([("id", ASCENDING)], unique=True)
    await db.jobs.create_index([("created_at", DESCENDING)])
    await db.jobs.create_index([("status", ASCENDING), ("queue_position", ASCENDING)])
    await db.steps.create_index([("job_id", ASCENDING), ("index", ASCENDING)], unique=True)
    await db.notifications.create_index([("id", ASCENDING)], unique=True)
    await db.notifications.create_index([("created_at", DESCENDING)])
    await app_settings.seed(db, now_iso())
    await providers.ensure_indexes(db)
    if await providers.migrate(db, now_iso()):
        logger.info("migrated the arena2api URL setting into the default provider")
    try:  # F-006: job repos moved out of backend/ (uvicorn --reload watch) to R2A_DATA_DIR
        await asyncio.to_thread(repos.migrate_legacy_data)
    except Exception:  # noqa: BLE001 - never block startup; repos that did not move are reported
        logging.getLogger("roadmap2arena").exception("Data migration from backend/data failed")
    await repos.ensure_indexes(db)
    await github_integration.ensure_indexes(db)
    if notifier.on_job_finished not in orchestrator.on_job_finished:
        orchestrator.on_job_finished.append(notifier.on_job_finished)
    if github_integration.on_job_finished not in orchestrator.on_job_finished:
        orchestrator.on_job_finished.append(github_integration.on_job_finished)  # auto-push (if enabled)
    for hooks, cb in ((orchestrator.on_run_start, git_integration.on_run_start),
                      (orchestrator.on_step_done, git_integration.on_step_done),
                      (deletion_routes.on_delete, git_integration.on_delete),
                      (deletion_routes.on_delete, github_integration.on_delete)):
        if cb not in hooks:
            hooks.append(cb)
    stale = await db.jobs.find({"status": "running"}, {"_id": 0, "id": 1}).to_list(None)
    for job in stale:
        ts = now_iso()
        running = await db.steps.find_one({"job_id": job["id"], "status": "running"}, {"_id": 0, "index": 1})
        await db.steps.update_many({"job_id": job["id"], "status": "running"},
                                   {"$set": {"status": "error", "error": INTERRUPTED, "finished_at": ts}})
        await db.jobs.update_one({"id": job["id"]}, {"$set": {
            "status": "error", "error": f"Job {INTERRUPTED}", "finished_at": ts, "updated_at": ts,
            "failed_step": running["index"] if running else None}})
        await append_log(db, job["id"], "error", f"Job {INTERRUPTED}")
    if stale:
        logger.warning("marked %d running job(s) as interrupted", len(stale))
    # Queued/paused jobs survive a restart; the worker starts the next one.
    async with scheduler.lock:
        await scheduler.renumber(db)
    scheduler.start_worker(db)
    github_integration.start_poller()  # in-process: keep uvicorn at --workers 1
    yield
    await github_integration.stop_poller()
    await scheduler.stop_worker()
    mongo.close()


app = FastAPI(title="ROADMAP2ARENA", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, exc: RequestValidationError):
    """Return the standard 422 shape but never echo the submitted input back (B-004).

    FastAPI's default handler includes each error's raw "input", which can contain
    secrets such as the SMTP password sent to /api/notifications/settings.
    """
    errors = [{k: v for k, v in err.items() if k not in ("input", "ctx")} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
api = APIRouter(prefix="/api")


# ------------------------------------------------------------------ models
class ParseRequest(BaseModel):
    roadmap_md: str


class JobCreate(BaseModel):
    arena_url: str | None = None
    model: str | None = None
    project_context: str = ""
    roadmap_md: str = Field(default="")
    cloned_from: str | None = None  # source job id when submitted from "Clone job"
    provider_id: str | None = None  # see contracts.md "Providers"


async def resolve_provider(provider_id: str | None, arena_url: str | None, *, use_default: bool) -> dict | None:
    """Provider doc for a new run, or None for a legacy (arena_url) job. 422 for an unknown id."""
    if provider_id is not None:
        doc = await providers.get_doc(db, provider_id)
        if not doc:
            raise HTTPException(status_code=422, detail=f"provider_id: provider {provider_id} not found")
        return doc
    if arena_url is not None or not use_default:
        return None
    default = await providers.default_id(db)
    return await providers.get_doc(db, default) if default else None


def validate_job_input(body: JobCreate, cfg: dict, provider: dict | None = None) -> tuple[str, str, list[dict]]:
    if provider:
        arena_url = provider["base_url"]
        model = (body.model if body.model is not None else (provider.get("default_model") or cfg["model"])).strip()
    else:
        arena_url = (body.arena_url if body.arena_url is not None else cfg["arena_url"]).strip()
        model = (body.model if body.model is not None else cfg["model"]).strip()
    errors = []
    parsed = urlparse(arena_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append("arena_url must be an http:// or https:// URL")
    if not model:
        errors.append("model must not be empty")
    steps = parse_roadmap(body.roadmap_md) if body.roadmap_md.strip() else []
    if not body.roadmap_md.strip():
        errors.append("roadmap_md must not be empty")
    elif not steps:
        errors.append("No steps found in roadmap_md (use ### headings or - [ ] items)")
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return arena_url, model, steps


class JobOverrides(BaseModel):
    """Optional overrides for restart/resume (e.g. to fix a wrong model or switch provider)."""
    arena_url: str | None = None
    model: str | None = None
    provider_id: str | None = None


async def override_provider(body: JobOverrides | None, job: dict) -> dict | None:
    """Provider snapshot for a resumed/restarted run: provider_id switches, an arena_url
    override alone makes it a legacy job, otherwise the job keeps its snapshot."""
    if body and body.provider_id is not None:
        return providers.snapshot(await resolve_provider(body.provider_id, None, use_default=False))
    if body and body.arena_url is not None:
        return None
    return job.get("provider")


def validate_overrides(body: JobOverrides | None, job: dict, provider: dict | None = None) -> tuple[str, str]:
    if provider is not None:
        arena_url = provider["base_url"]
    else:
        arena_url = (body.arena_url if body and body.arena_url is not None else job["arena_url"]).strip()
    model = (body.model if body and body.model is not None else job["model"]).strip()
    errors = []
    parsed = urlparse(arena_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append("arena_url must be an http:// or https:// URL")
    if not model:
        errors.append("model must not be empty")
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return arena_url, model


async def insert_job(arena_url: str, model: str, project_context: str, roadmap_md: str,
                     steps: list[dict], restarted_from: str | None = None, cloned_from: str | None = None,
                     provider: dict | None = None) -> str:
    """Insert a job at the end of the queue. Call under scheduler.lock."""
    job_id = str(uuid.uuid4())
    ts = now_iso()
    await db.jobs.insert_one({
        "id": job_id, "status": "queued", "queue_position": await scheduler.end_position(db),
        "queued_at": ts, "started_at": None, "created_at": ts, "updated_at": ts, "finished_at": None,
        "arena_url": arena_url, "model": model, "project_context": project_context,
        # non-secret provider snapshot {id, name, preset, base_url}; None = legacy arena_url job
        "provider": provider,
        "roadmap_md": roadmap_md, "title": roadmap_title(roadmap_md) or steps[0]["title"],
        "step_total": len(steps), "steps_done": 0, "error": None, "failed_step": None,
        "stopped_step": None, "restarted_from": restarted_from, "cloned_from": cloned_from, "log": [],
        # project_id: owning project (planned "Projects" feature, None = standalone job);
        # repo_id: the job's own git repo (repos collection), set when it is created.
        "project_id": None, "repo_id": None,
    })
    await db.steps.insert_many([{
        "job_id": job_id, "index": s["index"], "title": s["title"], "description": s["description"],
        "status": "pending", "prompt": "", "response": "", "error": None, "artifacts": [],
        "started_at": None, "finished_at": None,
    } for s in steps])
    if restarted_from:
        await append_log(db, job_id, "info", f"Restart of job {restarted_from}")
    if cloned_from:
        await append_log(db, job_id, "info", f"Clone of job {cloned_from}")
    await append_log(db, job_id, "info", "Added to the queue")
    return job_id


async def queued_note(job_id: str) -> None:
    state = await queue_state(job_id)
    if state["status"] == "queued":
        await append_log(db, job_id, "info", f"Waiting in the queue at position {state['queue_position']}")


async def get_job_or_404(job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


# ------------------------------------------------------------------ routes
@api.get("/config")
async def get_config():
    """Form defaults; same values as GET /api/settings (kept for compatibility)."""
    cfg = await app_settings.get(db)
    return {k: cfg[k] for k in app_settings.FIELDS}


@api.post("/roadmap/parse")
async def parse(body: ParseRequest):
    steps = parse_roadmap(body.roadmap_md)
    if not steps:
        raise HTTPException(status_code=422, detail="No steps found (use ### headings or - [ ] items)")
    return {"steps": steps, "title": roadmap_title(body.roadmap_md)}


@api.post("/jobs", status_code=201)
async def create_job(body: JobCreate):
    provider = await resolve_provider(body.provider_id, body.arena_url, use_default=True)
    arena_url, model, steps = validate_job_input(body, await app_settings.get(db), provider)
    snap = providers.snapshot(provider) if provider else None
    if body.cloned_from is not None and not await db.jobs.find_one({"id": body.cloned_from}, {"_id": 1}):
        raise HTTPException(status_code=422, detail=f"cloned_from: job {body.cloned_from} not found")
    async with scheduler.lock:
        job_id = await insert_job(arena_url, model, body.project_context, body.roadmap_md, steps,
                                  cloned_from=body.cloned_from, provider=snap)
        await scheduler.start_next_locked(db)
        await queued_note(job_id)
        state = await queue_state(job_id)
    return {"job_id": job_id, **state}


LIST_FIELDS = ("project_id", "status", "created_at", "step_total", "steps_done", "title", "model", "provider", "failed_step", "stopped_step",
               "restarted_from", "cloned_from", "queue_position", "queued_at", "started_at", "finished_at")


@api.get("/jobs")
async def list_jobs(status: str | None = None,
                    limit: int = Query(LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX)):
    """Most recent jobs first. ?status=done,error filters (comma-separated)."""
    query: dict = {}
    if status is not None:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in ALL_STATUSES]
        if bad or not wanted:
            raise HTTPException(status_code=422, detail=f"status must be a comma-separated list of {', '.join(ALL_STATUSES)}")
        query["status"] = {"$in": wanted}
    cursor = db.jobs.find(query, {"_id": 0, "id": 1, **{k: 1 for k in LIST_FIELDS}}).sort("created_at", -1).limit(limit)
    return [{"job_id": j["id"], **{k: j.get(k) for k in LIST_FIELDS}, "paused": j["status"] == "paused"}
            async for j in cursor]


@api.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await get_job_or_404(job_id)
    steps = await db.steps.find({"job_id": job_id}, {"_id": 0, "prompt": 0, "response": 0}).sort("index", 1).to_list(None)
    return {
        "job_id": job["id"],
        "status": job["status"],
        "error": job.get("error"),
        "failed_step": job.get("failed_step"),
        "stopped_step": job.get("stopped_step"),
        "restarted_from": job.get("restarted_from"),
        "cloned_from": job.get("cloned_from"),
        "project_id": job.get("project_id"),
        "repo_id": job.get("repo_id"),
        "queue_position": job.get("queue_position"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "title": job.get("title"),
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
        "arena_url": job["arena_url"],
        "model": job["model"],
        "provider": job.get("provider"),
        "project_context": job.get("project_context", ""),
        "roadmap_md": job.get("roadmap_md", ""),
        "step_total": job["step_total"],
        "steps_done": job["steps_done"],
        "steps": [{
            "index": s["index"], "title": s["title"], "description": s.get("description", ""),
            "status": s["status"], "error": s.get("error"),
            "artifact_paths": [a["path"] for a in s.get("artifacts", [])],
            "commit_sha": s.get("commit_sha"),
        } for s in steps],
        "log": job.get("log", []),
    }


@api.get("/jobs/{job_id}/steps/{index}")
async def get_step(job_id: str, index: int):
    await get_job_or_404(job_id)
    if not 1 <= index <= MAX_STEP_INDEX:
        raise HTTPException(status_code=404, detail=f"Step {index} of job {job_id} not found")
    step = await db.steps.find_one({"job_id": job_id, "index": index}, {"_id": 0})
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {index} of job {job_id} not found")
    return step


@api.post("/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    job = await get_job_or_404(job_id)
    if job["status"] != "running":
        hint = " - remove it from the queue instead" if job["status"] in scheduler.IN_QUEUE else ""
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, only a running job can be stopped{hint}")
    if not await orchestrator.stop(db, job_id):
        # No task yet/anymore. Take the start lock so a resume that already set the
        # job running has also started its task, then decide.
        async with scheduler.lock:
            if orchestrator.is_running(job_id):
                stopping = True
            else:
                stopping = False
                await orchestrator.mark_stopped(db, job_id, "operator (no active task)")
        if stopping:
            await orchestrator.stop(db, job_id)
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "status": 1, "stopped_step": 1, "steps_done": 1})
    if job["status"] != "stopped":
        # Finished naturally (or failed) before the stop could take effect.
        raise HTTPException(status_code=409, detail=f"Job finished as {job['status']} before it could be stopped")
    return {"job_id": job_id, "status": job["status"], "stopped_step": job.get("stopped_step"),
            "steps_done": job["steps_done"]}


@api.post("/jobs/{job_id}/restart", status_code=201)
async def restart_job(job_id: str, body: JobOverrides | None = None):
    job = await get_job_or_404(job_id)
    if job["status"] not in FINISHED:
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, only a finished job can be restarted")
    provider = await override_provider(body, job)
    arena_url, model = validate_overrides(body, job, provider)
    steps = parse_roadmap(job["roadmap_md"])
    async with scheduler.lock:
        # One active restart per job: guards against double clicks / parallel requests.
        active = await db.jobs.find_one({"restarted_from": job_id, "status": {"$in": ["queued", "paused", "running"]}},
                                        {"_id": 0, "id": 1, "status": 1})
        if active:
            raise HTTPException(status_code=409, detail=f"A restart of this job is already {active['status']} (job {active['id']})")
        new_id = await insert_job(arena_url, model, job.get("project_context", ""), job["roadmap_md"], steps,
                                  restarted_from=job_id, provider=provider)
        await scheduler.start_next_locked(db)
        await queued_note(new_id)
        state = await queue_state(new_id)
    return {"job_id": new_id, **state}


@api.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, body: JobOverrides | None = None):
    job = await get_job_or_404(job_id)
    if job["status"] not in RESUMABLE:
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, only an error, stopped or cancelled job can be resumed")
    provider = await override_provider(body, job)
    arena_url, model = validate_overrides(body, job, provider)
    async with scheduler.lock:
        current = await db.jobs.find_one({"id": job_id}, {"_id": 0, "status": 1})
        if current["status"] not in RESUMABLE:  # e.g. a parallel resume already queued it
            raise HTTPException(status_code=409, detail=f"Job is {current['status']}, only an error, stopped or cancelled job can be resumed")
        first = await db.steps.find_one({"job_id": job_id, "status": {"$ne": "done"}}, {"_id": 0, "index": 1},
                                        sort=[("index", 1)])
        if not first:
            raise HTTPException(status_code=409, detail="All steps are already done - nothing to resume")
        k = first["index"]
        ts = now_iso()
        await db.steps.update_many({"job_id": job_id, "index": {"$gte": k}}, {"$set": {
            "status": "pending", "prompt": "", "response": "", "artifacts": [], "error": None, "commit_sha": None,
            "started_at": None, "finished_at": None,
        }})
        done = await db.steps.count_documents({"job_id": job_id, "status": "done"})
        await db.jobs.update_one({"id": job_id}, {"$set": {
            "status": "queued", "queue_position": await scheduler.end_position(db), "queued_at": ts,
            "error": None, "failed_step": None, "stopped_step": None, "finished_at": None,
            "arena_url": arena_url, "model": model, "provider": provider, "steps_done": done, "updated_at": ts,
        }})
        changes = []
        old_provider = job.get("provider") or {}
        if old_provider.get("id") != (provider or {}).get("id"):
            changes.append(f"provider {old_provider.get('name') or 'legacy URL'} -> {(provider or {}).get('name') or 'legacy URL'}")
        if arena_url != job["arena_url"]:
            changes.append(f"arena_url {job['arena_url']} -> {arena_url}")
        if model != job["model"]:
            changes.append(f"model {job['model']} -> {model}")
        await append_log(db, job_id, "info", f"Resumed from step {k}" + (f" ({'; '.join(changes)})" if changes else ""))
        await scheduler.start_next_locked(db)
        await queued_note(job_id)
        state = await queue_state(job_id)
    return {"job_id": job_id, **state, "resumed_from_step": k}


@api.get("/jobs/{job_id}/download")
async def download(job_id: str):
    await get_job_or_404(job_id)
    latest: dict[str, str] = {}
    async for s in db.steps.find({"job_id": job_id, "status": "done"}, {"_id": 0, "artifacts": 1}).sort("index", 1):
        for a in s.get("artifacts", []):
            latest[a["path"]] = a["content"]
    if not latest:
        raise HTTPException(status_code=409, detail="No artifacts yet - nothing to download")

    buf = io.BytesIO()
    written: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for raw, content in latest.items():
            path, reason = clean_zip_path(raw)
            if reason:
                await append_log(db, job_id, "warn", f'ZIP: skipped "{raw}" - {reason}')
                continue
            if path in written:
                await append_log(db, job_id, "warn", f'ZIP: skipped "{raw}" - duplicate of {path}')
                continue
            written.add(path)
            zf.writestr(path, content if content.endswith("\n") else content + "\n")
    await append_log(db, job_id, "ok", f"ZIP: packed {len(written)} files")
    buf.seek(0)
    filename = f"roadmap2arena-{job_id[:8]}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


app.include_router(api)
app.include_router(queue_router)
app.include_router(history_router)
app.include_router(deletion_router)
app.include_router(notification_router)
app.include_router(git_router)
app.include_router(github_router)
app.include_router(provider_router)
