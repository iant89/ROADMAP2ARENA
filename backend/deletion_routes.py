"""Hard deletion of jobs (job document, its steps and their artifacts).

DELETE /api/jobs/{id}           delete one job (409 if it is running - stop it first)
POST   /api/jobs/bulk-delete    delete several jobs; running/unknown ids are reported as skipped
POST   /api/jobs/delete-finished  delete every finished job (done, error, stopped, cancelled)

Queued/paused jobs are taken out of the queue (positions renumbered). Everything runs
under scheduler.lock, which also serialises job starts, so a queued job cannot start
while it is being deleted. Other modules can register cleanup callbacks in `on_delete`
(e.g. a per-job git repo); a failing callback is logged and never blocks the delete.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import orchestrator
import scheduler
from database import db

router = APIRouter(prefix="/api")
logger = logging.getLogger("roadmap2arena.deletion")

FINISHED = ("done", "error", "stopped", "cancelled")
BULK_MAX = 500
# async callbacks (db, job_id) run after a job has been deleted
on_delete: list[Callable[[object, str], Awaitable[None]]] = []


class Skipped(Exception):
    def __init__(self, reason: str, status: str | None = None):
        super().__init__(reason)
        self.reason, self.status = reason, status


async def _delete_locked(job_id: str) -> dict:
    """Delete one job. Call under scheduler.lock. Raises Skipped('not_found'|'running')."""
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "id": 1, "status": 1})
    if not job:
        raise Skipped("not_found")
    if job["status"] == "running" or orchestrator.is_running(job_id):
        raise Skipped("running", job["status"])
    steps = await db.steps.delete_many({"job_id": job_id})
    await db.jobs.delete_one({"id": job_id})
    for cb in on_delete:
        try:
            await cb(db, job_id)
        except Exception:  # noqa: BLE001 - cleanup must not break deletion
            logger.exception("on_delete callback failed for job %s", job_id)
    logger.info("deleted job %s (%s, %d steps)", job_id, job["status"], steps.deleted_count)
    return {"job_id": job_id, "previous_status": job["status"], "steps_deleted": steps.deleted_count,
            "was_queued": job["status"] in scheduler.IN_QUEUE}


async def _delete_many_locked(ids: list[str]) -> tuple[list[dict], list[dict]]:
    deleted, skipped = [], []
    for job_id in ids:
        try:
            deleted.append(await _delete_locked(job_id))
        except Skipped as s:
            skipped.append({"job_id": job_id, "reason": s.reason})
    if any(d["was_queued"] for d in deleted):
        await scheduler.renumber(db)
    return deleted, skipped


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    async with scheduler.lock:
        try:
            res = await _delete_locked(job_id)
        except Skipped as s:
            if s.reason == "not_found":
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
            raise HTTPException(status_code=409, detail="Job is running - stop it first, then delete it")
        if res["was_queued"]:
            await scheduler.renumber(db)
    return {"deleted": True, **res}


class BulkDelete(BaseModel):
    job_ids: list[str] = Field(..., min_length=1, max_length=BULK_MAX)


@router.post("/jobs/bulk-delete")
async def bulk_delete(body: BulkDelete):
    ids = list(dict.fromkeys(i.strip() for i in body.job_ids if i.strip()))
    if not ids:
        raise HTTPException(status_code=422, detail="job_ids must contain at least one id")
    async with scheduler.lock:
        deleted, skipped = await _delete_many_locked(ids)
    return {"deleted": [d["job_id"] for d in deleted], "deleted_count": len(deleted), "skipped": skipped}


@router.post("/jobs/delete-finished")
async def delete_finished():
    async with scheduler.lock:
        ids = [j["id"] async for j in db.jobs.find({"status": {"$in": list(FINISHED)}}, {"_id": 0, "id": 1})]
        deleted, skipped = await _delete_many_locked(ids)
    return {"deleted": [d["job_id"] for d in deleted], "deleted_count": len(deleted), "skipped": skipped}
