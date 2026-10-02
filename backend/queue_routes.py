"""Routes for the job queue (/api/queue) and runtime settings (/api/settings)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

import app_settings
import scheduler
from database import db
from orchestrator import now_iso

router = APIRouter(prefix="/api")

SUMMARY_FIELDS = ("status", "title", "model", "step_total", "steps_done", "queue_position", "queued_at",
                  "created_at", "started_at", "restarted_from")


def summary(job: dict) -> dict:
    return {"job_id": job["id"], **{k: job.get(k) for k in SUMMARY_FIELDS}, "paused": job["status"] == "paused"}


async def queue_state(job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "status": 1, "queue_position": 1})
    return {"status": job["status"], "queue_position": job.get("queue_position")}


async def queued_job_or_error(job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "id": 1, "status": 1})
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job["status"] not in scheduler.IN_QUEUE:
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not in the queue")
    return job


# ------------------------------------------------------------------ queue
@router.get("/queue")
async def get_queue():
    running = await db.jobs.find_one({"status": "running"}, {"_id": 0, "log": 0})
    queued = await scheduler.ordered_queue(db)
    return {
        "running": summary(running) if running else None,
        "queued": [summary(j) for j in queued],
        "count": len(queued),
        "waiting": sum(1 for j in queued if j["status"] == "queued"),
    }


class MoveBody(BaseModel):
    direction: Literal["up", "down"] | None = None
    position: int | None = None


@router.post("/queue/{job_id}/move")
async def move(job_id: str, body: MoveBody):
    if (body.direction is None) == (body.position is None):
        raise HTTPException(status_code=422, detail="Send exactly one of direction (up|down) or position")
    if body.position is not None and body.position < 1:
        raise HTTPException(status_code=422, detail="position must be 1 or greater")
    async with scheduler.lock:
        await queued_job_or_error(job_id)
        await scheduler.move_locked(db, job_id, body.direction, body.position)
        return {"job_id": job_id, **await queue_state(job_id)}


@router.post("/queue/{job_id}/pause")
async def pause(job_id: str):
    async with scheduler.lock:
        job = await queued_job_or_error(job_id)
        if job["status"] == "queued":
            await scheduler.pause_locked(db, job_id)
        return {"job_id": job_id, **await queue_state(job_id)}


@router.post("/queue/{job_id}/unpause")
async def unpause(job_id: str):
    async with scheduler.lock:
        job = await queued_job_or_error(job_id)
        if job["status"] == "paused":
            await scheduler.unpause_locked(db, job_id)
            await scheduler.start_next_locked(db)
        return {"job_id": job_id, **await queue_state(job_id)}


@router.delete("/queue/{job_id}")
async def remove(job_id: str):
    async with scheduler.lock:
        await queued_job_or_error(job_id)
        await scheduler.cancel_locked(db, job_id)
        return {"job_id": job_id, **await queue_state(job_id)}


# ------------------------------------------------------------------ settings
@router.get("/settings")
async def get_settings():
    return app_settings.public(await app_settings.get(db))


@router.put("/settings")
async def put_settings(body: dict = Body(...)):
    unknown = sorted(set(body) - set(app_settings.FIELDS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown setting(s): {', '.join(unknown)}")
    merged = {**await app_settings.get(db), **body}
    errors = app_settings.validate(merged)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return app_settings.public(await app_settings.save(db, merged, now_iso()))


@router.post("/settings/reset")
async def reset_settings():
    return app_settings.public(await app_settings.reset(db, now_iso()))
