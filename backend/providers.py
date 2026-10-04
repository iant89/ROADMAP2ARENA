"""OpenAI-compatible providers: storage, validation, key encryption, model listing, error mapping.

A provider is a named base URL (the full OpenAI base, usually ending in /v1), an optional API
key (Fernet-encrypted with R2A_SECRET_KEY, write-only) and optional non-secret extra headers.
See contracts.md "Providers". The API key is never returned, logged, copied to jobs, or echoed
in an error message: every provider error text goes through redact() first.
"""
from __future__ import annotations

import re
import time
import uuid
from urllib.parse import urlparse

import httpx
import openai
from cryptography.fernet import InvalidToken

import app_settings
import secret_key

SETTINGS_ID = app_settings.SETTINGS_ID
NAME_MAX = 60
MODEL_MAX = 200
HEADERS_MAX = 10
HEADER_VALUE_MAX = 500
API_KEY_MAX = 4096
MODELS_MAX = 1000
HTTP_TIMEOUT = 15.0
DETAIL_MAX = 200
REDACTED = "[redacted]"
HINT_503 = "check that the arena2api Chrome tab is open and pushing tokens"
HINT_401_GATEWAY = ("check that ARENA2API_API_KEY in backend/.env matches GATEWAY_API_KEY in gateway/.env - the key is "
                    "only sent when the job URL is exactly ARENA2API_URL")
LEGACY_NAME = "arena2api"

PRESETS = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True,
     "hint": "API key from platform.openai.com"},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
     "hint": "API key from openrouter.ai/keys; optional headers HTTP-Referer and X-Title"},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "needs_key": True,
     "hint": "API key from console.groq.com"},
    {"id": "ollama", "label": "Ollama (local)", "base_url": "http://localhost:11434/v1", "needs_key": False,
     "hint": "No key needed; pull a model first (ollama pull <model>)"},
    {"id": "lmstudio", "label": "LM Studio (local)", "base_url": "http://localhost:1234/v1", "needs_key": False,
     "hint": "Start the local server in LM Studio; no key needed"},
    {"id": "arena2api", "label": "arena2api (local)", "base_url": "http://localhost:9090/v1", "needs_key": False,
     "hint": "Local arena2api gateway; key only if the gateway has one configured"},
    {"id": "custom", "label": "Custom", "base_url": "", "needs_key": False,
     "hint": "Any OpenAI-compatible API; the base URL usually ends in /v1"},
]
PRESET_IDS = {p["id"] for p in PRESETS}
TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$")  # RFC 7230 header field name
BLOCKED_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "host",
                   "content-length", "content-type", "connection", "transfer-encoding"}
EDITABLE = {"name", "preset", "base_url", "headers", "default_model", "api_key", "clear_api_key", "make_default"}


class ProviderError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class ProviderUnavailable(Exception):
    """Raised instead of a request when a job's provider cannot be used (deleted, bad key)."""


# ------------------------------------------------------------------ validation
def _printable(value: str) -> bool:
    return all(32 <= ord(c) <= 126 for c in value)


def normalize_base_url(value) -> str:
    if not isinstance(value, str):
        raise ProviderError(422, "base_url must be an http:// or https:// URL")
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment \
            or parsed.username or parsed.password or not _printable(url) or len(url) > 500:
        raise ProviderError(422, "base_url must be an http:// or https:// URL without credentials, query or fragment")
    return url


def validate_headers(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError(422, "headers must be an object of name -> value")
    if len(value) > HEADERS_MAX:
        raise ProviderError(422, f"at most {HEADERS_MAX} extra headers")
    out: dict[str, str] = {}
    seen: set[str] = set()
    for name, val in value.items():
        if not isinstance(name, str) or not TOKEN_RE.match(name.strip()):
            raise ProviderError(422, "header names must be HTTP tokens (letters, digits, -)")
        name = name.strip()
        if name.lower() in BLOCKED_HEADERS:
            raise ProviderError(422, f"header {name} is not allowed here - put secrets in the API key field")
        if name.lower() in seen:
            raise ProviderError(422, f"duplicate header {name}")
        if not isinstance(val, str) or not _printable(val) or len(val) > HEADER_VALUE_MAX:
            raise ProviderError(422, f"header {name}: value must be printable ASCII, max {HEADER_VALUE_MAX} chars")
        seen.add(name.lower())
        out[name] = val.strip()
    return out


def _validate_name(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > NAME_MAX:
        raise ProviderError(422, f"name must be 1-{NAME_MAX} characters")
    return value.strip()


def _validate_model(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > MODEL_MAX:
        raise ProviderError(422, f"default_model must be a string of at most {MODEL_MAX} characters")
    return value.strip() or None


def _validate_key(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(422, "api_key must be a non-empty string (use clear_api_key to remove it)")
    key = value.strip()
    if not _printable(key) or len(key) > API_KEY_MAX:
        raise ProviderError(422, "api_key must be printable ASCII")  # never echo it
    return key


# ------------------------------------------------------------------ secrets
def _require_fernet():
    f = secret_key.fernet()
    if f is None:
        state = secret_key.key_state()
        raise ProviderError(409, f"R2A_SECRET_KEY is {'not set' if state == 'missing' else 'not a valid Fernet key'} - "
                                 f"it encrypts provider API keys. {secret_key.KEY_HINT}.")
    return f


def encrypt_key(key: str) -> str:
    return _require_fernet().encrypt(key.encode()).decode()


def decrypt_key(doc: dict) -> tuple[str | None, str | None]:
    """(key, error). (None, None) when the provider has no key."""
    token = doc.get("api_key_enc")
    if not token:
        return None, None
    f = secret_key.fernet()
    if f is None:
        return None, "R2A_SECRET_KEY is missing or invalid - the stored API key cannot be read"
    try:
        return f.decrypt(token.encode()).decode(), None
    except InvalidToken:
        return None, "the stored API key cannot be decrypted (R2A_SECRET_KEY changed?) - re-enter it"


def redact(text: str, secrets: list[str | None]) -> str:
    """Scrub provider secrets AND the server-only ARENA2API_API_KEY (PR #11) from any message."""
    out = app_settings.redact_gateway_key(text or "")
    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(s, REDACTED)
    return out


def secrets_of(key: str | None, headers: dict | None) -> list[str]:
    return [key or "", *((headers or {}).values())]


# ------------------------------------------------------------------ public shapes
def public(doc: dict, default_id: str | None) -> dict:
    _, key_error = decrypt_key(doc)
    return {
        "id": doc["id"], "name": doc["name"], "preset": doc.get("preset", "custom"), "base_url": doc["base_url"],
        "headers": doc.get("headers") or {}, "default_model": doc.get("default_model"),
        "api_key_set": bool(doc.get("api_key_enc")), "api_key_error": key_error,
        "server_key": not doc.get("api_key_enc") and server_key_for(doc["base_url"]) is not None,
        "is_default": doc["id"] == default_id, "migrated": bool(doc.get("migrated")),
        "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at"),
    }


def snapshot(doc: dict) -> dict:
    """Non-secret copy stored on jobs."""
    return {"id": doc["id"], "name": doc["name"], "preset": doc.get("preset", "custom"), "base_url": doc["base_url"]}


def origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) - a stored key is only ever sent to the origin it was saved for."""
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None)
    return parsed.scheme, (parsed.hostname or "").lower(), port


def legacy_base(arena_url: str) -> str:
    base = arena_url.strip().rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def server_key_for(base_url: str) -> str | None:
    """The server-only ARENA2API_API_KEY (backend/.env) for a provider WITHOUT its own key.

    Same exact-URL rule as app_settings.gateway_api_key for legacy jobs: only when base_url is
    exactly ARENA2API_URL's OpenAI base (ARENA2API_URL + /v1). Aliases (127.0.0.1 vs localhost),
    other ports or paths never receive it. The key itself is never stored or returned.
    """
    key = app_settings.env.ARENA2API_API_KEY
    if not key or not base_url:
        return None
    return key if base_url.strip().rstrip("/") == legacy_base(app_settings.env.ARENA2API_URL) else None


def effective_key(stored_key: str | None, base_url: str) -> str | None:
    """A provider's own key wins; otherwise the server gateway key for the exact gateway URL."""
    return stored_key or server_key_for(base_url)


# ------------------------------------------------------------------ storage
async def ensure_indexes(db) -> None:
    await db.providers.create_index("id", unique=True)


async def default_id(db) -> str | None:
    doc = await db.settings.find_one({"_id": SETTINGS_ID}, {"default_provider_id": 1})
    return (doc or {}).get("default_provider_id")


async def set_default(db, provider_id: str | None) -> None:
    await db.settings.update_one({"_id": SETTINGS_ID}, {"$set": {"default_provider_id": provider_id}}, upsert=True)


async def get_doc(db, provider_id: str) -> dict | None:
    if not isinstance(provider_id, str):
        return None
    return await db.providers.find_one({"id": provider_id}, {"_id": 0})


async def list_docs(db) -> list[dict]:
    docs = await db.providers.find({}, {"_id": 0}).to_list(None)
    return sorted(docs, key=lambda d: d["name"].lower())


async def _name_taken(db, name: str, exclude: str | None = None) -> bool:
    query = {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
    if exclude:
        query["id"] = {"$ne": exclude}
    return bool(await db.providers.find_one(query, {"_id": 1}))


async def create(db, body: dict, now: str) -> dict:
    unknown = sorted(set(body) - (EDITABLE - {"clear_api_key"}))
    if unknown:
        raise ProviderError(422, f"Unknown field(s): {', '.join(unknown)}")
    name = _validate_name(body.get("name"))
    preset = body.get("preset") or "custom"
    if preset not in PRESET_IDS:
        raise ProviderError(422, f"preset must be one of {', '.join(sorted(PRESET_IDS))}")
    doc = {"id": str(uuid.uuid4()), "name": name, "preset": preset, "base_url": normalize_base_url(body.get("base_url")),
           "headers": validate_headers(body.get("headers")), "default_model": _validate_model(body.get("default_model")),
           "api_key_enc": None, "migrated": False, "created_at": now, "updated_at": now}
    if body.get("api_key") is not None:
        doc["api_key_enc"] = encrypt_key(_validate_key(body["api_key"]))
    if await _name_taken(db, name):
        raise ProviderError(409, f"A provider named {name!r} already exists")
    await db.providers.insert_one(dict(doc))
    if body.get("make_default") or not await default_id(db):
        await set_default(db, doc["id"])
    return doc


async def update(db, provider_id: str, body: dict, now: str) -> dict:
    doc = await get_doc(db, provider_id)
    if not doc:
        raise ProviderError(404, f"Provider {provider_id} not found")
    unknown = sorted(set(body) - EDITABLE)
    if unknown:
        raise ProviderError(422, f"Unknown field(s): {', '.join(unknown)}")
    changes: dict = {}
    if "name" in body:
        changes["name"] = _validate_name(body["name"])
        if await _name_taken(db, changes["name"], exclude=provider_id):
            raise ProviderError(409, f"A provider named {changes['name']!r} already exists")
    if "preset" in body:
        if body["preset"] not in PRESET_IDS:
            raise ProviderError(422, f"preset must be one of {', '.join(sorted(PRESET_IDS))}")
        changes["preset"] = body["preset"]
    if "base_url" in body:
        changes["base_url"] = normalize_base_url(body["base_url"])
        moving = origin(changes["base_url"]) != origin(doc["base_url"])
        if moving and doc.get("api_key_enc") and body.get("api_key") is None and body.get("clear_api_key") is not True:
            raise ProviderError(422, "Re-enter the API key (or clear it) when changing the provider's host - "
                                     "a stored key is never sent to a different host")
    if "headers" in body:
        changes["headers"] = validate_headers(body["headers"])
    if "default_model" in body:
        changes["default_model"] = _validate_model(body["default_model"])
    if body.get("clear_api_key") is True:
        if body.get("api_key") is not None:
            raise ProviderError(422, "send either api_key or clear_api_key, not both")
        changes["api_key_enc"] = None
    elif body.get("api_key") is not None:
        changes["api_key_enc"] = encrypt_key(_validate_key(body["api_key"]))
    changes["updated_at"] = now
    await db.providers.update_one({"id": provider_id}, {"$set": changes})
    if body.get("make_default") is True:
        await set_default(db, provider_id)
    return await get_doc(db, provider_id)


async def delete(db, provider_id: str) -> None:
    if not await get_doc(db, provider_id):
        raise ProviderError(404, f"Provider {provider_id} not found")
    active = await db.jobs.count_documents({"provider.id": provider_id, "status": {"$in": ["queued", "paused", "running"]}})
    if active:
        raise ProviderError(409, f"{active} queued, paused or running job(s) use this provider - "
                                 "wait for them or remove them from the queue first")
    await db.providers.delete_one({"id": provider_id})
    if await default_id(db) == provider_id:
        await set_default(db, None)


async def migrate(db, now: str) -> bool:
    """Turn the legacy single arena_url setting into a default provider (once)."""
    settings_doc = await db.settings.find_one({"_id": SETTINGS_ID}) or {}
    if settings_doc.get("providers_migrated"):
        return False
    created = False
    if not await db.providers.count_documents({}):
        url = settings_doc.get("arena_url") or app_settings.env_defaults()["arena_url"]
        try:
            base = normalize_base_url(legacy_base(url))
        except ProviderError:
            base = "http://localhost:9090/v1"
        doc = {"id": str(uuid.uuid4()), "name": "arena2api (local)", "preset": "arena2api", "base_url": base,
               "headers": {}, "default_model": None, "api_key_enc": None, "migrated": True,
               "created_at": now, "updated_at": now}
        await db.providers.insert_one(doc)
        await set_default(db, doc["id"])
        created = True
    await db.settings.update_one({"_id": SETTINGS_ID}, {"$set": {"providers_migrated": True}}, upsert=True)
    return created


# ------------------------------------------------------------------ errors
def _detail_from(response: httpx.Response | None) -> str:
    if response is None:
        return ""
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = err.get("message") or ""
        else:
            detail = err or (body.get("detail") if isinstance(body, dict) else "") or ""
        if not isinstance(detail, str):
            detail = str(detail)
    except Exception:  # noqa: BLE001 - body may not be JSON
        detail = response.text or ""
    return " ".join(detail.split())


def status_message(code: int, detail: str, *, name: str, model: str | None = None, context: str = "job",
                   arena: bool = False, secrets: list[str | None] = ()) -> str:
    detail = redact(detail, list(secrets))[:DETAIL_MAX]
    msg = f"{name} returned {code}"
    tag, hint = "", ""
    if code == 401 and arena:
        # PR #11 wording for the gateway; provider jobs may also carry their own key.
        hint = HINT_401_GATEWAY if name == LEGACY_NAME else f"{HINT_401_GATEWAY}, or set this provider's API key in Settings > Providers"
    elif code == 401:
        tag, hint = " (API key rejected)", "check the API key in Settings > Providers"
    elif code == 403:
        tag, hint = " (access denied)", "the key may lack access to this model, or the account needs credits"
    elif code == 404:
        hint = (f"model '{model}' not found or wrong base URL; check the model name or fetch the model list"
                if context == "job" else "endpoint not found; check the base URL (it usually ends in /v1)")
    elif code == 429:
        tag, hint = " (rate limit or quota exceeded)", "wait, then resume the job" if context == "job" else "wait and try again"
    elif code >= 500:
        tag = " (server error)" if not (arena and code == 503) else ""
        if arena and code == 503:
            hint = HINT_503
    msg += tag
    if detail:
        msg += f": {detail}"
    if hint:
        msg += f" - {hint}"
    return redact(msg, list(secrets))


def describe(exc: Exception, *, name: str, base_url: str, model: str | None, timeout: float, arena: bool,
             secrets: list[str | None], context: str = "job") -> str:
    if isinstance(exc, ProviderUnavailable):
        msg = str(exc)
    elif isinstance(exc, openai.APIStatusError):
        msg = status_message(exc.status_code, _detail_from(exc.response), name=name, model=model, context=context,
                             arena=arena, secrets=secrets)
    elif isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException)):
        msg = f"{name} request timed out after {timeout:g}s"
    elif isinstance(exc, (openai.APIConnectionError, httpx.TransportError)):
        msg = f"could not reach {name} at {base_url} - is it running?"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return redact(msg, secrets)


# ------------------------------------------------------------------ HTTP
def request_headers(key: str | None, headers: dict | None) -> dict:
    out = {"Accept": "application/json", **(headers or {})}
    if key:
        out["Authorization"] = f"Bearer {key}"
    return out


def parse_models(payload) -> list[str]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and items is None:
        items = payload.get("models")
    if not isinstance(items, list):
        raise ValueError("unexpected response (no model list)")
    ids = set()
    for item in items:
        mid = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if isinstance(mid, str) and mid.strip():
            ids.add(mid.strip())
    return sorted(ids)[:MODELS_MAX]


async def fetch_models(base_url: str, key: str | None, headers: dict | None, *, name: str,
                       arena: bool = False, timeout: float = HTTP_TIMEOUT) -> list[str]:
    """GET {base_url}/models. Raises ProviderError(502, mapped message) on any failure."""
    secrets = secrets_of(key, headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            r = await client.get(f"{base_url.rstrip('/')}/models", headers=request_headers(key, headers))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(502, describe(exc, name=name, base_url=base_url, model=None, timeout=timeout,
                                          arena=arena, secrets=secrets, context="models")) from None
    if r.status_code >= 300:
        raise ProviderError(502, status_message(r.status_code, _detail_from(r), name=name, context="models",
                                                arena=arena, secrets=secrets))
    try:
        return parse_models(r.json())
    except ValueError as exc:
        raise ProviderError(502, redact(f"{name} returned an unexpected /models response ({exc}) - "
                                        "type the model name manually", secrets)) from None


async def test_connection(base_url: str, key: str | None, headers: dict | None, *, name: str, arena: bool) -> dict:
    t = time.monotonic()
    try:
        models = await fetch_models(base_url, key, headers, name=name, arena=arena)
    except ProviderError as e:
        return {"ok": False, "status": None, "model_count": 0, "models": [], "latency_ms": int((time.monotonic() - t) * 1000),
                "message": e.detail}
    return {"ok": True, "status": 200, "model_count": len(models), "models": models[:200],
            "latency_ms": int((time.monotonic() - t) * 1000),
            "message": f"Connected - {len(models)} model{'s' if len(models) != 1 else ''} available"}


# ------------------------------------------------------------------ jobs
async def resolve_for_run(db, job: dict) -> dict:
    """Connection parameters for a run: {name, base_url, key, headers, arena}. Raises ProviderUnavailable."""
    snap = job.get("provider")
    if not snap:
        # Legacy job: PR #11's rule - the server key only for exactly ARENA2API_URL.
        return {"name": LEGACY_NAME, "base_url": legacy_base(job["arena_url"]),
                "key": app_settings.gateway_api_key(job["arena_url"]), "headers": {}, "arena": True}
    doc = await get_doc(db, snap["id"])
    if not doc:
        raise ProviderUnavailable(f"Provider '{snap['name']}' was deleted - resume or restart the job with another provider")
    key, err = decrypt_key(doc)
    if err:
        raise ProviderUnavailable(f"Provider '{doc['name']}': {err} in Settings > Providers")
    # The provider's CURRENT base URL is used (the stored key belongs to it); the orchestrator
    # refreshes the job's snapshot when it changed since the job was created.
    return {"name": doc["name"], "base_url": doc["base_url"], "key": effective_key(key, doc["base_url"]),
            "headers": doc.get("headers") or {},
            "arena": doc.get("preset") == "arena2api", "snapshot": snapshot(doc)}
