"""Runs a job: sends each roadmap step to arena2api in order and stores results.

Every state change is written to MongoDB so GET /api/jobs/{id} always reflects
progress. Fail-fast: the first failing step marks the job as error and later
steps stay pending.

Job controls: a running job can be stopped (its task is cancelled, the
in-flight arena request is abandoned, the running step becomes "stopped").
A job in error/stopped can be resumed: run_job skips steps that are already
done, rebuilding the chat history and the file list from their stored
prompts, responses and artifacts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import openai

import settings
from arena_client import ArenaClient, build_prompt
from artifact_extractor import count_unnamed_blocks, extract_artifacts

LOG_LIMIT = 500
HINT_503 = "check that the arena2api Chrome tab is open and pushing tokens"

logger = logging.getLogger("roadmap2arena.orchestrator")
# Registry of running job tasks by job_id (single uvicorn process).
_tasks: dict[str, asyncio.Task] = {}
# Jobs whose cancellation was requested by an operator (vs. server shutdown).
_stop_requested: set[str] = set()
STOP_WAIT_SECONDS = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def append_log(db, job_id: str, level: str, msg: str) -> None:
    entry = {"ts": now_iso(), "level": level, "msg": msg}
    await db.jobs.update_one(
        {"id": job_id},
        {"$push": {"log": {"$each": [entry], "$slice": -LOG_LIMIT}}, "$set": {"updated_at": entry["ts"]}},
    )


def describe_error(exc: Exception, arena_url: str) -> str:
    if isinstance(exc, openai.APIStatusError):
        code = exc.status_code
        msg = f"arena2api returned {code}"
        detail = ""
        try:
            body = exc.response.json()
            err = body.get("error") if isinstance(body, dict) else None
            detail = err.get("message", "") if isinstance(err, dict) else (err or body.get("detail") or "")
        except Exception:  # noqa: BLE001 - body may not be JSON
            detail = (exc.response.text or "")[:200]
        if detail:
            msg += f": {detail}"
        if code == 503:
            msg += f" - {HINT_503}"
        return msg
    if isinstance(exc, openai.APITimeoutError):
        return f"arena2api request timed out after {settings.ARENA_REQUEST_TIMEOUT_SECONDS:g}s"
    if isinstance(exc, openai.APIConnectionError):
        return f"could not reach arena2api at {arena_url} - is it running?"
    return f"{type(exc).__name__}: {exc}"


def is_running(job_id: str | None = None) -> bool:
    if job_id is not None:
        task = _tasks.get(job_id)
        return bool(task and not task.done())
    return any(not t.done() for t in _tasks.values())


def start(db, job_id: str) -> None:
    task = asyncio.create_task(run_job(db, job_id), name=f"job-{job_id}")
    _tasks[job_id] = task

    def _cleanup(_t: asyncio.Task) -> None:
        if _tasks.get(job_id) is task:
            _tasks.pop(job_id, None)
        _stop_requested.discard(job_id)

    task.add_done_callback(_cleanup)


async def stop(db, job_id: str) -> bool:
    """Cancel a running job's task and wait until it has recorded the stop.

    Returns False if no task is running for this job in this process.
    """
    task = _tasks.get(job_id)
    if not task or task.done():
        return False
    _stop_requested.add(job_id)
    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=STOP_WAIT_SECONDS)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    return True


async def mark_stopped(db, job_id: str, reason: str) -> None:
    """Record a stopped job: running step -> stopped, later steps stay pending."""
    ts = now_iso()
    running = await db.steps.find_one({"job_id": job_id, "status": "running"}, {"_id": 0, "index": 1})
    await db.steps.update_many(
        {"job_id": job_id, "status": "running"},
        {"$set": {"status": "stopped", "error": None, "finished_at": ts}},
    )
    done = await db.steps.count_documents({"job_id": job_id, "status": "done"})
    await db.jobs.update_one({"id": job_id}, {"$set": {
        "status": "stopped", "error": None, "failed_step": None, "steps_done": done,
        "stopped_step": running["index"] if running else None, "finished_at": ts, "updated_at": ts,
    }})
    where = f" at step {running['index']}" if running else " between steps"
    await append_log(db, job_id, "warn", f"Job stopped{where} by {reason} - later steps left pending")


async def run_job(db, job_id: str) -> None:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    steps = await db.steps.find({"job_id": job_id}, {"_id": 0}).sort("index", 1).to_list(None)
    client = ArenaClient(job["arena_url"], job["model"], settings.ARENA_REQUEST_TIMEOUT_SECONDS)
    files: dict[str, int] = {}

    # Resume support: replay finished steps into the chat history and file list.
    done_steps = [s for s in steps if s["status"] == "done"]
    for s in done_steps:
        client.add_turn(s.get("prompt", ""), s.get("response", ""))
        for a in s.get("artifacts", []):
            files[a["path"]] = s["index"]
    todo = [s for s in steps if s["status"] != "done"]
    if done_steps:
        await append_log(db, job_id, "info", f"Rebuilt history from {len(done_steps)} done step(s) "
                                             f"({len(client.history)} messages, {len(files)} files); "
                                             f"model {job['model']}, arena2api {job['arena_url']}")
    else:
        await append_log(db, job_id, "info", f"Job started: {len(steps)} steps, model {job['model']}, arena2api {job['arena_url']}")
    try:
        for step in todo:
            idx = step["index"]
            prompt = build_prompt(job.get("project_context", ""), steps, idx, sorted(files))
            await db.steps.update_one(
                {"job_id": job_id, "index": idx},
                {"$set": {"status": "running", "prompt": prompt, "started_at": now_iso()}},
            )
            await append_log(db, job_id, "info", f'Step {idx}/{len(steps)} "{step["title"]}" sent to arena2api')
            try:
                response = await client.complete(prompt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure ends the job
                message = describe_error(exc, job["arena_url"])
                ts = now_iso()
                await db.steps.update_one(
                    {"job_id": job_id, "index": idx},
                    {"$set": {"status": "error", "error": message, "finished_at": ts}},
                )
                await db.jobs.update_one(
                    {"id": job_id},
                    {"$set": {"status": "error", "error": f"Step {idx} failed: {message}", "failed_step": idx,
                              "finished_at": ts, "updated_at": ts}},
                )
                await append_log(db, job_id, "error", f"Step {idx} failed: {message}")
                if len(steps) > idx:
                    await append_log(db, job_id, "error", f"Job stopped - steps {idx + 1}-{len(steps)} left pending")
                return

            artifacts = [{"path": p, "content": c} for p, c in extract_artifacts(response).items()]
            unnamed = count_unnamed_blocks(response)
            replaced = [f"{a['path']} (from step {files[a['path']]})" for a in artifacts if a["path"] in files]
            for a in artifacts:
                files[a["path"]] = idx
            ts = now_iso()
            # Only a still-running step may become done (guards against a racing stop).
            res = await db.steps.update_one(
                {"job_id": job_id, "index": idx, "status": "running"},
                {"$set": {"status": "done", "response": response, "artifacts": artifacts, "finished_at": ts}},
            )
            if res.modified_count:
                await db.jobs.update_one({"id": job_id}, {"$inc": {"steps_done": 1}, "$set": {"updated_at": ts}})
            note = f"{len(artifacts)} file{'s' if len(artifacts) != 1 else ''}"
            extras = []
            if unnamed:
                extras.append(f"{unnamed} unnamed block{'s' if unnamed != 1 else ''} kept in transcript")
            if replaced:
                extras.append("replaced " + ", ".join(replaced))
            if extras:
                note += f" ({'; '.join(extras)})"
            await append_log(db, job_id, "ok", f"Step {idx} done - {note}")
            if idx < len(steps) and settings.ARENA_STEP_DELAY_SECONDS > 0:
                await asyncio.sleep(settings.ARENA_STEP_DELAY_SECONDS)

        ts = now_iso()
        await db.jobs.update_one({"id": job_id, "status": "running"},
                                 {"$set": {"status": "done", "finished_at": ts, "updated_at": ts}})
        await append_log(db, job_id, "ok", f"Job finished - {len(steps)}/{len(steps)} steps, {len(files)} files")
    except asyncio.CancelledError:
        if job_id in _stop_requested:
            await mark_stopped(db, job_id, "operator")
        else:
            ts = now_iso()
            await db.jobs.update_one({"id": job_id}, {"$set": {"status": "error", "error": "Job cancelled (server shutdown)",
                                                                "finished_at": ts, "updated_at": ts}})
            await db.steps.update_many({"job_id": job_id, "status": "running"},
                                       {"$set": {"status": "error", "error": "cancelled (server shutdown)"}})
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected internal error
        logger.exception("job %s crashed", job_id)
        ts = now_iso()
        await db.jobs.update_one({"id": job_id}, {"$set": {"status": "error", "error": f"Internal error: {exc}",
                                                            "finished_at": ts, "updated_at": ts}})
        await append_log(db, job_id, "error", f"Internal error: {exc}")
    finally:
        await client.close()
