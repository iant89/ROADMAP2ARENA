"""Routes for notifications (/api/notifications...) and their settings."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

import notifier
import notify_settings
from database import db
from orchestrator import now_iso

router = APIRouter(prefix="/api/notifications")
FIELDS = {"_id": 0, "id": 1, "event": 1, "job_id": 1, "title": 1, "status": 1, "step": 1, "message": 1, "url": 1,
          "created_at": 1, "read": 1, "deliveries": 1}


# ------------------------------------------------------------------ settings
@router.get("/settings")
async def get_settings():
    return notify_settings.public(await notify_settings.get(db))


@router.put("/settings")
async def put_settings(body: dict = Body(...)):
    merged, errors = notify_settings.merge(await notify_settings.get(db), body)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return notify_settings.public(await notify_settings.save(db, merged, now_iso()))


def _test_cfg(section: str, current: dict, override: dict | None) -> dict:
    """Saved channel settings with optional unsaved overrides from the form (validated)."""
    if not override:
        return current[section]
    merged, errors = notify_settings.merge(current, {section: {**override, "enabled": False}})
    errors = [e for e in errors if e.startswith(section) or e.startswith(f"unknown {section}") or e.startswith("email ")]
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return merged[section]


@router.post("/test/webhook")
async def test_webhook(body: dict | None = Body(None)):
    """Send a test payload to the saved URL, or to body.url (not saved). Works even if disabled."""
    cfg = _test_cfg("webhook", await notify_settings.get(db), body)
    if not cfg["url"]:
        raise HTTPException(status_code=422, detail="webhook.url is not set")
    payload = notifier.build_event("test", None, (await notify_settings.get(db))["app_url"])
    return {**await notifier.send_webhook(cfg["url"], payload), "url": cfg["url"]}


@router.post("/test/email")
async def test_email(body: dict | None = Body(None)):
    """Send a test email with the saved SMTP settings, or with unsaved overrides from body
    (an omitted/masked password uses the saved one). Works even if email is disabled."""
    cfg = _test_cfg("email", await notify_settings.get(db), body)
    payload = notifier.build_event("test", None, (await notify_settings.get(db))["app_url"])
    res = await notifier.send_email(cfg, payload)
    return {**res, "to_addrs": cfg["to_addrs"]}


# ------------------------------------------------------------------ list / read / clear
@router.get("")
async def list_notifications(limit: int = Query(50, ge=1, le=200), unread: bool = False):
    query = {"read": False} if unread else {}
    items = await db.notifications.find(query, FIELDS).sort("created_at", -1).limit(limit).to_list(None)
    return {"items": items, "unread_count": await db.notifications.count_documents({"read": False}),
            "total": await db.notifications.count_documents({})}


@router.post("/read-all")
async def read_all():
    res = await db.notifications.update_many({"read": False}, {"$set": {"read": True}})
    return {"updated": res.modified_count, "unread_count": 0}


@router.post("/{notif_id}/read")
async def mark_read(notif_id: str):
    res = await db.notifications.update_one({"id": notif_id}, {"$set": {"read": True}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail=f"Notification {notif_id} not found")
    return {"id": notif_id, "read": True, "unread_count": await db.notifications.count_documents({"read": False})}


@router.delete("/{notif_id}")
async def delete_one(notif_id: str):
    if notif_id == "settings":  # B-005: don't treat the settings path as a notification id
        raise HTTPException(status_code=405, detail="Method Not Allowed", headers={"Allow": "GET, PUT"})
    res = await db.notifications.delete_one({"id": notif_id})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail=f"Notification {notif_id} not found")
    return {"id": notif_id, "deleted": True}


@router.delete("")
async def clear_all():
    res = await db.notifications.delete_many({})
    return {"deleted": res.deleted_count, "unread_count": 0}
