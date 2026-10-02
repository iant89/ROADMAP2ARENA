"""Notifications for job events: stored in MongoDB (bell + toasts) and sent to a webhook / SMTP.

Events: job_done, job_failed, job_stopped (from orchestrator.on_job_finished) and
queue_empty (a job finished and no queued job is left; paused jobs don't count).
Delivery failures are logged (server log + job log + the notification's `deliveries`)
and never affect jobs. The SMTP password is only read here, never returned.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
import uuid
from email.message import EmailMessage

import httpx

import notify_settings
from orchestrator import append_log, now_iso

logger = logging.getLogger("roadmap2arena.notifier")
KEEP = 500  # notifications kept in the collection
WEBHOOK_TIMEOUT = 10.0
SMTP_TIMEOUT = 15.0
ICONS = {"job_done": "\u2705", "job_failed": "\u274c", "job_stopped": "\u23f9\ufe0f", "queue_empty": "\U0001f4ed",
         "test": "\U0001f514", "github_pushed": "\u2b06\ufe0f", "github_pr_opened": "\U0001f500",
         "github_pr_merged": "\U0001f7e3", "github_pr_closed": "\u26d4", "github_checks_passed": "\u2705",
         "github_checks_failed": "\u274c"}
LABELS = {"job_done": "Job finished", "job_failed": "Job failed", "job_stopped": "Job stopped", "queue_empty": "Queue empty",
          "test": "Test notification", "github_pushed": "Pushed to GitHub", "github_pr_opened": "PR opened",
          "github_pr_merged": "PR merged", "github_pr_closed": "PR closed", "github_checks_passed": "Checks passed",
          "github_checks_failed": "Checks failed"}


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "\u2026"


def build_event(event: str, job: dict | None, app_url: str, paused: int = 0, message: str | None = None,
                link: str | None = None) -> dict:
    """The notification / webhook payload for an event. `message`/`link` are prebuilt by GitHub events
    (link = the GitHub URL; url stays the in-app link)."""
    title = (job or {}).get("title") or ("Untitled roadmap" if job else None)
    step = None
    if message is not None:
        pass
    elif event == "job_done":
        step = job.get("step_total")
        message = f"Job finished: {title} - {job.get('steps_done')}/{job.get('step_total')} steps"
    elif event == "job_failed":
        step = job.get("failed_step")
        reason = re.sub(r"^Step \d+ failed:\s*", "", job.get("error") or "")  # avoid "at step 1 - Step 1 failed:"
        message = f"Job failed: {title}" + (f" at step {step}" if step else "") + (f" - {_clip(reason, 300)}" if reason else "")
    elif event == "job_stopped":
        step = job.get("stopped_step")
        message = f"Job stopped: {title}" + (f" at step {step}" if step else "") + \
                  f" ({job.get('steps_done')}/{job.get('step_total')} steps done)"
    elif event == "queue_empty":
        message = "Queue empty - all queued jobs have finished" + (f" ({paused} paused job{'s' if paused != 1 else ''} left)" if paused else "")
    else:  # test
        message = "Test notification from ROADMAP2ARENA - webhook/email delivery works"
    job_id = (job or {}).get("id")
    url = f"{app_url}/?tab=history&job={job_id}" if job_id else f"{app_url}/?tab=queue"
    human = f"{ICONS.get(event, '')} {message}\n" + (f"{link}\n" if link else "") + url
    human = human.strip()
    return {
        "event": event, "job_id": job_id, "title": title, "status": (job or {}).get("status"), "step": step,
        "message": message, "url": url, "link": link, "timestamp": now_iso(), "app": "ROADMAP2ARENA",
        "text": human,                 # Slack incoming webhooks
        "content": human if len(human) <= 1900 else human[:1899] + "\u2026",  # Discord (2000 char limit)
    }


# ------------------------------------------------------------------ delivery
async def send_webhook(url: str, payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as client:
            r = await client.post(url, json=payload, headers={"User-Agent": "ROADMAP2ARENA-notifier"})
        if 200 <= r.status_code < 300:
            return {"ok": True, "status_code": r.status_code, "error": None}
        return {"ok": False, "status_code": r.status_code, "error": f"HTTP {r.status_code}: {_clip(r.text, 200)}"}
    except httpx.TimeoutException:
        return {"ok": False, "status_code": None, "error": f"timed out after {WEBHOOK_TIMEOUT:g}s"}
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {_clip(str(exc), 200) or 'connection failed'}"}


def _smtp_send(cfg: dict, msg: EmailMessage) -> None:
    host, port, sec = cfg["host"], int(cfg["port"]), cfg["security"]
    if sec == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    try:
        server.ehlo()
        if sec == "starttls":
            if not server.has_extn("starttls"):
                raise smtplib.SMTPNotSupportedError("server does not offer STARTTLS (choose security 'none' or 'ssl')")
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            server.close()


def build_email(cfg: dict, payload: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[ROADMAP2ARENA] {LABELS.get(payload['event'], payload['event'])}" + \
                     (f": {payload['title']}" if payload.get("title") else "")
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to_addrs"])
    lines = [payload["message"], ""] + ([f"GitHub: {payload['link']}"] if payload.get("link") else []) + \
            [f"Open: {payload['url']}", ""]
    if payload.get("job_id"):
        lines += [f"Job: {payload['job_id']}", f"Status: {payload['status']}"]
    lines += [f"Event: {payload['event']}", f"Time: {payload['timestamp']}"]
    msg.set_content("\n".join(lines))
    return msg


async def send_email(cfg: dict, payload: dict) -> dict:
    missing = [k for k in ("host", "from_addr") if not cfg.get(k)] + ([] if cfg.get("to_addrs") else ["to_addrs"])
    if missing:
        return {"ok": False, "error": f"missing {', '.join(missing)}"}
    try:
        await asyncio.to_thread(_smtp_send, cfg, build_email(cfg, payload))
        return {"ok": True, "error": None}
    except smtplib.SMTPAuthenticationError as exc:
        return {"ok": False, "error": f"authentication failed ({exc.smtp_code})"}
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(str(exc), 200) or 'SMTP error'}"}


# ------------------------------------------------------------------ dispatch
async def _record(db, notif_id: str | None, job_id: str | None, channel: str, res: dict) -> None:
    ok = res["ok"]
    text = f"Notification: {channel} " + ("delivered" + (f" (HTTP {res['status_code']})" if res.get("status_code") else "")
                                          if ok else f"failed - {res['error']}")
    (logger.info if ok else logger.warning)("%s (job %s)", text, job_id)
    if job_id:
        await append_log(db, job_id, "info" if ok else "warn", text)
    if notif_id:
        await db.notifications.update_one({"id": notif_id}, {"$set": {f"deliveries.{channel}": {**res, "at": now_iso()}}})


async def notify(db, event: str, job: dict | None, paused: int = 0, message: str | None = None,
                 link: str | None = None) -> dict | None:
    """Store (if in-app is on for this event) and send to the enabled external channels."""
    cfg = await notify_settings.get(db)
    payload = build_event(event, job, cfg["app_url"], paused, message, link)
    notif_id = None
    if cfg["in_app"]["events"].get(event):
        notif_id = uuid.uuid4().hex
        await db.notifications.insert_one({**payload, "id": notif_id, "created_at": payload["timestamp"],
                                           "read": False, "deliveries": {}})
        await _trim(db)
    job_id = payload["job_id"]
    tasks = []
    if cfg["webhook"]["enabled"] and cfg["webhook"]["url"] and cfg["webhook"]["events"].get(event):
        tasks.append(("webhook", send_webhook(cfg["webhook"]["url"], payload)))
    if cfg["email"]["enabled"] and cfg["email"]["events"].get(event):
        tasks.append(("email", send_email(cfg["email"], payload)))
    for channel, coro in tasks:
        try:
            res = await coro
        except Exception as exc:  # noqa: BLE001 - delivery must never raise
            logger.exception("notification %s crashed", channel)
            res = {"ok": False, "error": f"internal error: {exc}"}
        try:
            await _record(db, notif_id, job_id, channel, res)
        except Exception:  # noqa: BLE001
            logger.exception("could not record %s delivery", channel)
    return payload


async def _trim(db) -> None:
    n = await db.notifications.count_documents({})
    if n > KEEP:
        old = await db.notifications.find({}, {"_id": 1}).sort("created_at", 1).limit(n - KEEP).to_list(None)
        await db.notifications.delete_many({"_id": {"$in": [d["_id"] for d in old]}})


EVENT_FOR_STATUS = {"done": "job_done", "error": "job_failed", "stopped": "job_stopped"}


async def on_job_finished(db, job_id: str, status: str) -> None:
    """orchestrator.on_job_finished listener: job event, then queue_empty if nothing is waiting."""
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "log": 0})
    event = EVENT_FOR_STATUS.get(status)
    if job and event:
        await notify(db, event, job)
    if not await db.jobs.count_documents({"status": {"$in": ["queued", "running"]}}):
        paused = await db.jobs.count_documents({"status": "paused"})
        await notify(db, "queue_empty", None, paused)
