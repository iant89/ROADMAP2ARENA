"""Job queue: queued/paused jobs with dense queue positions and a worker that
starts the next eligible job whenever nothing is running.

Statuses involved: "queued" (waits its turn), "paused" (stays in the queue but
is skipped), "running" (exactly one at a time), "cancelled" (removed from the
queue, kept in history). Queue positions are 1..n over queued + paused jobs and
are only changed while holding `lock`, which also serialises job starts.
"""
from __future__ import annotations

import asyncio
import logging

import orchestrator
from orchestrator import append_log, now_iso

logger = logging.getLogger("roadmap2arena.scheduler")

IN_QUEUE = ("queued", "paused")
# Safety net: the worker re-checks the queue at least this often even without a wake-up.
POLL_SECONDS = 5.0

# Serialises queue changes, the "is anything running" check and job starts.
lock = asyncio.Lock()
_wake = asyncio.Event()
_worker: asyncio.Task | None = None
_shutting_down = False


def kick() -> None:
    """Ask the worker to look at the queue (safe from callbacks)."""
    _wake.set()


async def any_running(db) -> bool:
    return orchestrator.is_running() or bool(await db.jobs.find_one({"status": "running"}, {"_id": 1}))


async def ordered_queue(db) -> list[dict]:
    return await db.jobs.find({"status": {"$in": list(IN_QUEUE)}}, {"_id": 0, "log": 0}) \
        .sort([("queue_position", 1), ("queued_at", 1)]).to_list(None)


async def renumber(db, order: list[str] | None = None) -> None:
    """Rewrite queue positions as 1..n (in `order` if given, else current order). Call under lock."""
    if order is None:
        order = [j["id"] for j in await ordered_queue(db)]
    for pos, job_id in enumerate(order, start=1):
        await db.jobs.update_one({"id": job_id, "status": {"$in": list(IN_QUEUE)},
                                  "queue_position": {"$ne": pos}}, {"$set": {"queue_position": pos}})


async def end_position(db) -> int:
    return await db.jobs.count_documents({"status": {"$in": list(IN_QUEUE)}}) + 1


async def start_next_locked(db) -> str | None:
    """Start the first non-paused queued job if nothing is running. Call under lock."""
    if _shutting_down or await any_running(db):
        return None
    waiting = await db.jobs.count_documents({"status": "queued"})
    if not waiting:
        return None
    ts = now_iso()
    job = await db.jobs.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "queue_position": None, "started_at": ts, "updated_at": ts}},
        sort=[("queue_position", 1), ("queued_at", 1)],
        projection={"_id": 0, "id": 1, "queued_at": 1, "created_at": 1},
    )
    if not job:
        return None
    await renumber(db)
    await append_log(db, job["id"], "info", "Started from the queue")
    orchestrator.start(db, job["id"])
    logger.info("started job %s from the queue", job["id"])
    return job["id"]


async def _loop(db) -> None:
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
        _wake.clear()
        try:
            async with lock:
                await start_next_locked(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the worker alive
            logger.exception("queue worker failed to start the next job")


def start_worker(db) -> None:
    global _worker, _shutting_down
    _shutting_down = False
    orchestrator.on_job_end = kick
    _worker = asyncio.create_task(_loop(db), name="queue-worker")
    kick()


async def stop_worker() -> None:
    global _shutting_down
    _shutting_down = True
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except asyncio.CancelledError:
            pass


# ------------------------------------------------------------------ queue operations (call under lock)
async def move_locked(db, job_id: str, direction: str | None, position: int | None) -> list[str]:
    order = [j["id"] for j in await ordered_queue(db)]
    i = order.index(job_id)
    if direction == "up":
        target = max(0, i - 1)
    elif direction == "down":
        target = min(len(order) - 1, i + 1)
    else:
        target = min(max(position, 1), len(order)) - 1
    order.insert(target, order.pop(i))
    await renumber(db, order)
    return order


async def pause_locked(db, job_id: str) -> None:
    """queued -> paused and moved to the end of the queue."""
    ts = now_iso()
    await db.jobs.update_one({"id": job_id, "status": "queued"},
                             {"$set": {"status": "paused", "updated_at": ts}})
    order = [j["id"] for j in await ordered_queue(db) if j["id"] != job_id] + [job_id]
    await renumber(db, order)
    await append_log(db, job_id, "info", f"Paused - moved to the end of the queue (position {len(order)})")


async def unpause_locked(db, job_id: str) -> None:
    ts = now_iso()
    await db.jobs.update_one({"id": job_id, "status": "paused"}, {"$set": {"status": "queued", "updated_at": ts}})
    await append_log(db, job_id, "info", "Unpaused - eligible to run again")


async def cancel_locked(db, job_id: str) -> None:
    ts = now_iso()
    await db.jobs.update_one({"id": job_id, "status": {"$in": list(IN_QUEUE)}}, {"$set": {
        "status": "cancelled", "queue_position": None, "finished_at": ts, "updated_at": ts}})
    await renumber(db)
    await append_log(db, job_id, "warn", "Removed from the queue (cancelled)")
