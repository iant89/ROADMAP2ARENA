"""Runtime settings stored in MongoDB (collection "settings", one document).

Seeded from backend/.env on first startup; PUT /api/settings changes them and
"reset" restores the .env values. New job runs read the current values.
"""
from __future__ import annotations

from urllib.parse import urlparse

import settings as env

SETTINGS_ID = "app"
FIELDS = ("arena_url", "model", "step_delay_seconds", "request_timeout_seconds")
DELAY_RANGE = (0, 600)
TIMEOUT_RANGE = (10, 3600)


def gateway_api_key(arena_url: str) -> str | None:
    """Use the server credential only for the explicitly configured gateway URL.

    Jobs/settings can override arena_url. Never forward the gateway secret to an
    arbitrary URL, alias, port or path selected in the browser.
    """
    if arena_url.strip().rstrip("/") == env.ARENA2API_URL.strip().rstrip("/"):
        return env.ARENA2API_API_KEY or None
    return None


def redact_gateway_key(message: str) -> str:
    """Do not persist or return a server key if a provider echoes it in an error."""
    key = env.ARENA2API_API_KEY
    return message.replace(key, "[redacted gateway key]") if key else message


def env_defaults() -> dict:
    return {
        "arena_url": env.ARENA2API_URL,
        "model": env.ARENA2API_MODEL,
        "step_delay_seconds": _num(env.ARENA_STEP_DELAY_SECONDS),
        "request_timeout_seconds": _num(env.ARENA_REQUEST_TIMEOUT_SECONDS),
    }


def _clamp_env(values: dict) -> dict:
    """.env values outside the allowed ranges are clamped so the stored doc stays valid."""
    out = dict(values)
    out["step_delay_seconds"] = _num(min(max(float(out["step_delay_seconds"]), DELAY_RANGE[0]), DELAY_RANGE[1]))
    out["request_timeout_seconds"] = _num(min(max(float(out["request_timeout_seconds"]), TIMEOUT_RANGE[0]), TIMEOUT_RANGE[1]))
    return out


def _num(v) -> int | float:
    v = float(v)
    return int(v) if v.is_integer() else v


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate(values: dict) -> list[str]:
    """Returns a list of human-readable errors (empty when valid)."""
    errors = []
    url = values.get("arena_url")
    if not isinstance(url, str) or not is_http_url(url.strip()):
        errors.append("arena_url must be an http:// or https:// URL")
    model = values.get("model")
    if not isinstance(model, str) or not model.strip():
        errors.append("model must not be empty")
    for key, (lo, hi) in (("step_delay_seconds", DELAY_RANGE), ("request_timeout_seconds", TIMEOUT_RANGE)):
        v = values.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
            errors.append(f"{key} must be a number from {lo} to {hi}")
    return errors


def public(doc: dict) -> dict:
    return {**{k: doc[k] for k in FIELDS}, "updated_at": doc.get("updated_at"), "env_defaults": env_defaults()}


async def seed(db, now: str) -> None:
    await db.settings.update_one({"_id": SETTINGS_ID},
                                 {"$setOnInsert": {**_clamp_env(env_defaults()), "updated_at": now}}, upsert=True)


async def get(db) -> dict:
    doc = await db.settings.find_one({"_id": SETTINGS_ID})
    if not doc:  # should not happen after seed(); fall back to .env
        return {**_clamp_env(env_defaults()), "updated_at": None}
    return {k: v for k, v in doc.items() if k != "_id"}


async def save(db, values: dict, now: str) -> dict:
    doc = {k: values[k] for k in FIELDS}
    doc["arena_url"] = doc["arena_url"].strip()
    doc["model"] = doc["model"].strip()
    doc["step_delay_seconds"] = _num(doc["step_delay_seconds"])
    doc["request_timeout_seconds"] = _num(doc["request_timeout_seconds"])
    await db.settings.update_one({"_id": SETTINGS_ID}, {"$set": {**doc, "updated_at": now}}, upsert=True)
    return await get(db)


async def reset(db, now: str) -> dict:
    return await save(db, _clamp_env(env_defaults()), now)
