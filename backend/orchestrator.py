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

from typing import Awaitable, Callable

import openai

import app_settings
import providers
from arena_client import ArenaClient, build_prompt
from artifact_extractor import count_unnamed_blocks, extract_artifacts

LOG_LIMIT = 500
HINT_503 = providers.HINT_503
HINT_401 = providers.HINT_401_GATEWAY

logger = logging.getLogger("roadmap2arena.orchestrator")
# Registry of running job tasks by job_id (single uvicorn process).
_tasks: dict[str, asyncio.Task] = {}
# Jobs whose cancellation was requested by an operator (vs. server shutdown).
_stop_requested: set[str] = set()
# One in-flight stop operation per job: (operation task, job task it targets).
# Concurrent stop callers await the same operation instead of cancelling again.
_stop_ops: dict[str, tuple[asyncio.Task, asyncio.Task]] = {}
STOP_WAIT_SECONDS = 10
# Called when a job may have finished (set by the scheduler to wake the queue worker).
on_job_end: Callable[[], None] | None = None
# Async callbacks (db, job_id, status) run after a job reached done / error / stopped
# (e.g. notifications). Each runs in its own task; failures are logged and never affect the job.
on_job_finished: list[Callable[[object, str, str], Awaitable[None]]] = []
# Awaited hooks (inline, in order, shielded from a stop) - e.g. the per-job git history.
# They must not raise; exceptions are logged and ignored.
on_run_start: list[Callable[[object, str], Awaitable[None]]] = []          # (db, job_id)
on_step_done: list[Callable[[object, str, int], Awaitable[None]]] = []     # (db, job_id, step_index)
_finish_tasks: set[asyncio.Task] = set()


def _notify_end() -> None:
    if on_job_end:
        on_job_end()


def emit_finished(db, job_id: str, status: str) -> None:
    """Schedule the on_job_finished callbacks (fire and forget, shielded from the job task)."""
    for cb in on_job_finished:
        async def _run(cb=cb) -> None:
            try:
                await cb(db, job_id, status)
            except Exception:  # noqa: BLE001 - never let a listener break a job
                logger.exception("on_job_finished callback failed for job %s", job_id)
        t = asyncio.create_task(_run(), name=f"finished-{job_id}")
        _finish_tasks.add(t)
        t.add_done_callback(_finish_tasks.discard)


async def _run_hooks(hooks: list, *args) -> None:
    for cb in hooks:
        try:
            # shield: a stop request must not interrupt e.g. a git commit half-way
            await asyncio.shield(cb(*args))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - hooks never break a job
            logger.exception("orchestrator hook %s failed", getattr(cb, "__name__", cb))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def append_log(db, job_id: str, level: str, msg: str) -> None:
    entry = {"ts": now_iso(), "level": level, "msg": msg}
    await db.jobs.update_one(
        {"id": job_id},
        {"$push": {"log": {"$each": [entry], "$slice": -LOG_LIMIT}}, "$set": {"updated_at": entry["ts"]}},
    )


def describe_error(exc: Exception, arena_url: str, timeout_seconds: float, *, conn: dict | None = None,
                   model: str | None = None) -> str:
    """Readable, redacted step error (mapping: contracts.md "Providers" > Error mapping).

    conn is providers.resolve_for_run()'s result; without it the legacy arena2api wording is used.
    """
    conn = conn or {"name": providers.LEGACY_NAME, "base_url": arena_url, "key": None, "headers": {}, "arena": True}
    return providers.describe(exc, name=conn["name"], base_url=conn["base_url"], model=model, timeout=timeout_seconds,
                              arena=conn["arena"], secrets=providers.secrets_of(conn["key"], conn["headers"]))


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
        _notify_end()

    task.add_done_callback(_cleanup)


async def stop(db, job_id: str) -> bool:
    """Stop a running job; safe to call concurrently.

    The first caller starts a single stop operation that cancels the job task
    exactly once, waits for it to end and then records the stop. Later callers
    await that same operation. Returns False if no task is running for this
    job in this process.
    """
    task = _tasks.get(job_id)
    existing = _stop_ops.get(job_id)
    if existing and (task is None or existing[1] is task):
        await asyncio.shield(existing[0])
        return True
    if not task or task.done():
        return False
    op = asyncio.create_task(_stop_operation(db, job_id, task), name=f"stop-{job_id}")
    _stop_ops[job_id] = (op, task)

    def _cleanup(_t: asyncio.Task) -> None:
        if _stop_ops.get(job_id, (None,))[0] is op:
            _stop_ops.pop(job_id, None)

    op.add_done_callback(_cleanup)
    await asyncio.shield(op)
    return True


async def _stop_operation(db, job_id: str, task: asyncio.Task) -> None:
    _stop_requested.add(job_id)
    task.cancel()  # cancelled exactly once per job task
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=STOP_WAIT_SECONDS)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    except Exception:  # noqa: BLE001 - run_job handles its own errors
        logger.exception("job %s task ended with an error while stopping", job_id)
    # Finalise here, outside the cancelled task, so nothing can interrupt it.
    await mark_stopped(db, job_id, "operator")


async def mark_stopped(db, job_id: str, reason: str) -> bool:
    """Record a stopped job: running step -> stopped, later steps stay pending.

    Idempotent: only a job that is still "running" is changed (and logged), so a
    job that finished naturally just before the stop keeps its final status.
    Returns True if this call recorded the stop.
    """
    ts = now_iso()
    claimed = await db.jobs.find_one_and_update(
        {"id": job_id, "status": "running"},
        {"$set": {"status": "stopped", "error": None, "failed_step": None, "finished_at": ts, "updated_at": ts}},
        projection={"_id": 0, "id": 1},
    )
    if not claimed:
        return False
    running = await db.steps.find_one({"job_id": job_id, "status": "running"}, {"_id": 0, "index": 1})
    await db.steps.update_many(
        {"job_id": job_id, "status": "running"},
        {"$set": {"status": "stopped", "error": None, "finished_at": ts}},
    )
    done = await db.steps.count_documents({"job_id": job_id, "status": "done"})
    await db.jobs.update_one({"id": job_id}, {"$set": {
        "steps_done": done, "stopped_step": running["index"] if running else None,
    }})
    where = f" at step {running['index']}" if running else " between steps"
    await append_log(db, job_id, "warn", f"Job stopped{where} by {reason} - later steps left pending")
    emit_finished(db, job_id, "stopped")
    _notify_end()
    return True


async def _mark_shutdown(db, job_id: str) -> None:
    ts = now_iso()
    await db.jobs.update_one({"id": job_id, "status": "running"},
                             {"$set": {"status": "error", "error": "Job cancelled (server shutdown)",
                                       "finished_at": ts, "updated_at": ts}})
    await db.steps.update_many({"job_id": job_id, "status": "running"},
                               {"$set": {"status": "error", "error": "cancelled (server shutdown)"}})


async def run_job(db, job_id: str) -> None:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    steps = await db.steps.find({"job_id": job_id}, {"_id": 0}).sort("index", 1).to_list(None)
    # Current runtime settings (Mongo) apply to every new run, including resumes.
    cfg = await app_settings.get(db)
    delay, timeout = float(cfg["step_delay_seconds"]), float(cfg["request_timeout_seconds"])
    snap = job.get("provider")
    try:
        conn = await providers.resolve_for_run(db, job)
        unavailable = None
    except providers.ProviderUnavailable as exc:  # reported as a step-1 failure below
        conn = {"name": snap["name"], "base_url": snap["base_url"], "key": None, "headers": {}, "arena": False}
        unavailable = exc
    client = ArenaClient(conn["base_url"], job["model"], timeout, api_key=conn["key"], headers=conn["headers"])
    if snap and conn.get("snapshot") and conn["snapshot"] != snap:
        # Provider renamed/moved since the job was created: run against its current config.
        await db.jobs.update_one({"id": job_id}, {"$set": {"provider": conn["snapshot"], "arena_url": conn["base_url"]}})
        await append_log(db, job_id, "info", f"Provider changed since the job was created: {snap['name']} ({snap['base_url']}) -> "
                                             f"{conn['name']} ({conn['base_url']})")
    target = f"provider {conn['name']} ({conn['base_url']})" if snap else f"arena2api {job['arena_url']}"
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
                                             f"model {job['model']}, {target}, "
                                             f"step delay {delay:g}s, timeout {timeout:g}s")
    else:
        await append_log(db, job_id, "info", f"Job started: {len(steps)} steps, model {job['model']}, {target}, "
                                             f"step delay {delay:g}s, timeout {timeout:g}s")
    try:
        await _run_hooks(on_run_start, db, job_id)
        for step in todo:
            idx = step["index"]
            prompt = build_prompt(job.get("project_context", ""), steps, idx, sorted(files))
            await db.steps.update_one(
                {"job_id": job_id, "index": idx},
                {"$set": {"status": "running", "prompt": prompt, "started_at": now_iso()}},
            )
            await append_log(db, job_id, "info", f'Step {idx}/{len(steps)} "{step["title"]}" sent to {conn["name"]}')
            try:
                if unavailable:
                    raise unavailable
                response = await client.complete(prompt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure ends the job
                message = describe_error(exc, job["arena_url"], timeout, conn=conn, model=job["model"])
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
                emit_finished(db, job_id, "error")
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
            if res.modified_count:
                await _run_hooks(on_step_done, db, job_id, idx)
            if idx < len(steps) and delay > 0:
                await asyncio.sleep(delay)

        ts = now_iso()
        res = await db.jobs.update_one({"id": job_id, "status": "running"},
                                       {"$set": {"status": "done", "finished_at": ts, "updated_at": ts}})
        await append_log(db, job_id, "ok", f"Job finished - {len(steps)}/{len(steps)} steps, {len(files)} files")
        if res.modified_count:
            emit_finished(db, job_id, "done")
    except asyncio.CancelledError:
        # Operator stop: the stop operation records the result after this task ends.
        if job_id not in _stop_requested:
            await asyncio.shield(_mark_shutdown(db, job_id))
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected internal error
        logger.exception("job %s crashed", job_id)
        ts = now_iso()
        await db.jobs.update_one({"id": job_id}, {"$set": {"status": "error", "error": f"Internal error: {exc}",
                                                            "finished_at": ts, "updated_at": ts}})
        await append_log(db, job_id, "error", f"Internal error: {exc}")
        emit_finished(db, job_id, "error")
    finally:
        await client.close()
