"""Notification settings (MongoDB collection "notification_settings", one document).

Channels: in_app (bell + toasts + browser notifications), webhook, email (SMTP).
Each channel has per-event toggles for EVENTS. The SMTP password is write-only:
it is stored server-side and never returned (GET shows password_set + a mask).
"""
from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

JOB_EVENTS = ("job_done", "job_failed", "job_stopped", "queue_empty")
GITHUB_EVENTS = ("github_pushed", "github_pr_opened", "github_pr_merged", "github_pr_closed", "github_checks_passed",
                 "github_checks_failed")
EVENTS = JOB_EVENTS + GITHUB_EVENTS
SECURITY = ("starttls", "ssl", "none")
MASK = "********"
_DOC_ID = "singleton"
_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
_ALL_ON = {e: True for e in EVENTS}

DEFAULTS: dict = {
    "app_url": "http://localhost:8080",
    "in_app": {"events": dict(_ALL_ON)},
    "webhook": {"enabled": False, "url": "", "events": dict(_ALL_ON)},
    "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "", "password": "",
              "from_addr": "", "to_addrs": [], "events": {"job_done": True, "job_failed": True,
                                                          "job_stopped": False, "queue_empty": False,
                                                          **{e: False for e in GITHUB_EVENTS}}},
}
SECTION_KEYS = {
    "in_app": {"events"},
    "webhook": {"enabled", "url", "events"},
    "email": {"enabled", "host", "port", "security", "username", "password", "from_addr", "to_addrs", "events"},
}


def _http_url(value) -> bool:
    if not isinstance(value, str):
        return False
    u = urlparse(value.strip())
    return u.scheme in ("http", "https") and bool(u.netloc)


def parse_addrs(value) -> list[str] | None:
    """Accepts a list or a comma/semicolon/newline separated string. None if not a list/string."""
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return [v.strip() for v in value if v.strip()]


def merge(current: dict, patch: dict) -> tuple[dict, list[str]]:
    """Deep-merge a partial update into the current settings; returns (merged, errors).
    password: omitted or equal to the mask = keep, "" = clear, other string = set."""
    errors: list[str] = []
    out = copy.deepcopy(current)
    if not isinstance(patch, dict):
        return out, ["body must be a JSON object"]
    unknown = sorted(set(patch) - {"app_url", *SECTION_KEYS})
    if unknown:
        errors.append(f"unknown setting(s): {', '.join(unknown)}")
    if "app_url" in patch:
        if not _http_url(patch["app_url"]):
            errors.append("app_url must be an http(s) URL")
        else:
            out["app_url"] = patch["app_url"].strip().rstrip("/")
    for section, allowed in SECTION_KEYS.items():
        if section not in patch:
            continue
        sp = patch[section]
        if not isinstance(sp, dict):
            errors.append(f"{section} must be an object")
            continue
        bad = sorted(set(sp) - allowed - ({"password_set"} if section == "email" else set()))
        if bad:
            errors.append(f"unknown {section} setting(s): {', '.join(bad)}")
        for key, val in sp.items():
            if key not in allowed:
                continue
            if key == "events":
                if not isinstance(val, dict) or set(val) - set(EVENTS) or not all(isinstance(v, bool) for v in val.values()):
                    errors.append(f"{section}.events must map {', '.join(EVENTS)} to true/false")
                else:
                    out[section]["events"].update(val)
            elif key == "enabled":
                if not isinstance(val, bool):
                    errors.append(f"{section}.enabled must be true/false")
                else:
                    out[section]["enabled"] = val
            elif key == "port":
                if isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= 65535:
                    errors.append("email.port must be an integer 1-65535")
                else:
                    out["email"]["port"] = val
            elif key == "security":
                if val not in SECURITY:
                    errors.append(f"email.security must be one of {', '.join(SECURITY)}")
                else:
                    out["email"]["security"] = val
            elif key == "to_addrs":
                addrs = parse_addrs(val)
                if addrs is None:
                    errors.append("email.to_addrs must be a list or comma-separated string of addresses")
                else:
                    out["email"]["to_addrs"] = addrs
            elif key == "password":
                if not isinstance(val, str):
                    errors.append("email.password must be a string")
                elif val != MASK:
                    out["email"]["password"] = val
            else:  # url, host, username, from_addr
                if not isinstance(val, str):
                    errors.append(f"{section}.{key} must be a string")
                else:
                    out[section][key] = val.strip()
    errors += validate(out)
    return out, errors


def validate(s: dict) -> list[str]:
    errors = []
    w, e = s["webhook"], s["email"]
    if w["url"] and not _http_url(w["url"]):
        errors.append("webhook.url must be an http(s) URL")
    if w["enabled"] and not w["url"]:
        errors.append("webhook.url is required when the webhook is enabled")
    if e["from_addr"] and not _EMAIL_RE.match(e["from_addr"]):
        errors.append("email.from_addr is not a valid address")
    bad = [a for a in e["to_addrs"] if not _EMAIL_RE.match(a)]
    if bad:
        errors.append(f"email.to_addrs has invalid address(es): {', '.join(bad)}")
    if e["enabled"]:
        missing = [k for k in ("host", "from_addr") if not e[k]] + (["to_addrs"] if not e["to_addrs"] else [])
        if missing:
            errors.append(f"email {', '.join(missing)} required when email is enabled")
    if e["host"] and not re.match(r"^[A-Za-z0-9.\-\[\]:]+$", e["host"]):
        errors.append("email.host is not a valid host name")
    return errors


def public(s: dict) -> dict:
    """Settings as returned by the API: the SMTP password is never included."""
    out = copy.deepcopy(s)
    out.pop("_id", None)
    pw = out["email"].pop("password", "")
    out["email"]["password_set"] = bool(pw)
    out["email"]["password_masked"] = MASK if pw else ""
    return out


def _fill(doc: dict | None) -> dict:
    s = copy.deepcopy(DEFAULTS)
    if doc:
        if "app_url" in doc:
            s["app_url"] = doc["app_url"]
        for section in SECTION_KEYS:
            for k, v in (doc.get(section) or {}).items():
                if k == "events":
                    s[section]["events"].update(v)
                elif k in SECTION_KEYS[section]:
                    s[section][k] = v
        if doc.get("updated_at"):
            s["updated_at"] = doc["updated_at"]
    s.setdefault("updated_at", None)
    return s


async def get(db) -> dict:
    return _fill(await db.notification_settings.find_one({"_id": _DOC_ID}))


async def save(db, s: dict, ts: str) -> dict:
    doc = {k: v for k, v in s.items() if k != "updated_at"}
    doc["updated_at"] = ts
    await db.notification_settings.replace_one({"_id": _DOC_ID}, {"_id": _DOC_ID, **doc}, upsert=True)
    return _fill(doc)
