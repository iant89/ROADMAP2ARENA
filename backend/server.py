"""ROADMAP2ARENA backend: FastAPI app, all routes under /api."""
from __future__ import annotations

import io
import logging
import uuid
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING

import orchestrator
import settings
from artifact_extractor import clean_zip_path
from orchestrator import append_log, now_iso
from roadmap_parser import parse_roadmap, roadmap_title

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("roadmap2arena")

mongo = AsyncIOMotorClient(settings.MONGO_URL)
db = mongo[settings.DB_NAME]

INTERRUPTED = "interrupted by server restart"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.jobs.create_index([("id", ASCENDING)], unique=True)
    await db.jobs.create_index([("created_at", DESCENDING)])
    await db.steps.create_index([("job_id", ASCENDING), ("index", ASCENDING)], unique=True)
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
    yield
    mongo.close()


app = FastAPI(title="ROADMAP2ARENA", lifespan=lifespan)
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


def validate_job_input(body: JobCreate) -> tuple[str, str, list[dict]]:
    arena_url = (body.arena_url if body.arena_url is not None else settings.ARENA2API_URL).strip()
    model = (body.model if body.model is not None else settings.ARENA2API_MODEL).strip()
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


async def get_job_or_404(job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


# ------------------------------------------------------------------ routes
@api.get("/config")
async def get_config():
    return {
        "arena_url": settings.ARENA2API_URL,
        "model": settings.ARENA2API_MODEL,
        "step_delay_seconds": settings.ARENA_STEP_DELAY_SECONDS,
    }


@api.post("/roadmap/parse")
async def parse(body: ParseRequest):
    steps = parse_roadmap(body.roadmap_md)
    if not steps:
        raise HTTPException(status_code=422, detail="No steps found (use ### headings or - [ ] items)")
    return {"steps": steps, "title": roadmap_title(body.roadmap_md)}


@api.post("/jobs", status_code=201)
async def create_job(body: JobCreate):
    arena_url, model, steps = validate_job_input(body)
    if orchestrator.is_running() or await db.jobs.find_one({"status": "running"}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="A job is already running - wait for it to finish")
    job_id = str(uuid.uuid4())
    ts = now_iso()
    await db.jobs.insert_one({
        "id": job_id, "status": "running", "created_at": ts, "updated_at": ts, "finished_at": None,
        "arena_url": arena_url, "model": model, "project_context": body.project_context,
        "roadmap_md": body.roadmap_md, "title": roadmap_title(body.roadmap_md) or steps[0]["title"],
        "step_total": len(steps), "steps_done": 0, "error": None, "failed_step": None, "log": [],
    })
    await db.steps.insert_many([{
        "job_id": job_id, "index": s["index"], "title": s["title"], "description": s["description"],
        "status": "pending", "prompt": "", "response": "", "error": None, "artifacts": [],
        "started_at": None, "finished_at": None,
    } for s in steps])
    orchestrator.start(db, job_id)
    return {"job_id": job_id}


@api.get("/jobs")
async def list_jobs():
    cursor = db.jobs.find({}, {"_id": 0, "id": 1, "status": 1, "created_at": 1, "step_total": 1,
                               "steps_done": 1, "title": 1, "model": 1, "failed_step": 1}).sort("created_at", -1).limit(20)
    return [{"job_id": j.pop("id"), **j} async for j in cursor]


@api.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await get_job_or_404(job_id)
    steps = await db.steps.find({"job_id": job_id}, {"_id": 0, "prompt": 0, "response": 0}).sort("index", 1).to_list(None)
    return {
        "job_id": job["id"],
        "status": job["status"],
        "error": job.get("error"),
        "failed_step": job.get("failed_step"),
        "title": job.get("title"),
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
        "arena_url": job["arena_url"],
        "model": job["model"],
        "project_context": job.get("project_context", ""),
        "roadmap_md": job.get("roadmap_md", ""),
        "step_total": job["step_total"],
        "steps_done": job["steps_done"],
        "steps": [{
            "index": s["index"], "title": s["title"], "description": s.get("description", ""),
            "status": s["status"], "error": s.get("error"),
            "artifact_paths": [a["path"] for a in s.get("artifacts", [])],
        } for s in steps],
        "log": job.get("log", []),
    }


@api.get("/jobs/{job_id}/steps/{index}")
async def get_step(job_id: str, index: int):
    await get_job_or_404(job_id)
    step = await db.steps.find_one({"job_id": job_id, "index": index}, {"_id": 0})
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {index} of job {job_id} not found")
    return step


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
