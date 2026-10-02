"""GitHub REST client (raw httpx), secret redaction and encrypted token storage.

Token storage: MongoDB collection "integrations", _id "github", field ``token_enc`` - the
token Fernet-encrypted with R2A_SECRET_KEY from backend/.env. The same applies to both auth
methods (personal access token or OAuth device flow; they are mutually exclusive). The token
is never returned by the API, logged, put on a command line or written to .git/config.
R2A_GITHUB_TOKEN in backend/.env (optional) overrides the stored token.

The API base URL comes from GITHUB_API_URL (default https://api.github.com) so tests can point
it at a local stub; no test ever talks to the real GitHub.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from orchestrator import now_iso

logger = logging.getLogger("roadmap2arena.github")
API_VERSION = "2026-03-10"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DOC_ID = "github"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
FULL_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,100}$")
DEFAULT_AUTO_PUSH = {"enabled": False, "private": True}
KEY_HINT = ("Set a valid R2A_SECRET_KEY in backend/.env (generate one with: /app/venv/bin/python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\") and restart the backend - "
            "it encrypts the GitHub token at rest")
ENV_HINT = "R2A_GITHUB_TOKEN is set in backend/.env and overrides Settings - remove it there to connect from the UI"


def api_url() -> str:
    return (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")


def oauth_url() -> str:
    """Base URL for the OAuth device flow endpoints (github.com; a local stub in tests)."""
    return (os.environ.get("GITHUB_OAUTH_URL") or "https://github.com").rstrip("/")


def oauth_client(doc: dict | None) -> tuple[str | None, str | None]:
    """(client_id, source): a client ID saved in Settings wins over GITHUB_OAUTH_CLIENT_ID in .env."""
    saved = ((doc or {}).get("oauth_client_id") or "").strip()
    if saved:
        return saved, "settings"
    env = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "").strip()
    return (env, "env") if env else (None, None)


# ------------------------------------------------------------------ redaction
_TOKEN_PAT = re.compile(r"(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})")
_AUTH_PAT = re.compile(r"(?i)(authorization:\s*)(basic|bearer|token)\s+\S+")
_URL_CRED = re.compile(r"(https?://)[^/\s@]+@")


def redact(text, *secrets: str | None) -> str:
    """Remove tokens, Authorization values and URL credentials from text before it is logged/returned."""
    out = "" if text is None else str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    out = _TOKEN_PAT.sub("***", out)
    out = _AUTH_PAT.sub(r"\1\2 ***", out)
    return _URL_CRED.sub(r"\1***@", out)


# ------------------------------------------------------------------ errors
class GitHubError(Exception):
    """status: the HTTP status OUR API answers with; gh_status: GitHub's own status (None for network errors).

    kind: auth | forbidden | not_found | conflict | validation | rate_limited | legal | upstream | timeout | network
    """

    def __init__(self, status: int, message: str, gh_status: int | None = None, kind: str = "upstream",
                 retry_after: int | None = None):
        super().__init__(message)
        self.status, self.message, self.gh_status, self.kind, self.retry_after = status, message, gh_status, kind, retry_after


KIND_STATUS = {"auth": 401, "forbidden": 403, "not_found": 404, "conflict": 409, "validation": 422,
               "rate_limited": 429, "legal": 451, "timeout": 504}


def _gh_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:200]
    msg = body.get("message", "") if isinstance(body, dict) else ""
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list):
        extra = "; ".join(e.get("message") or f"{e.get('field', '')} {e.get('code', '')}".strip()
                          for e in errors if isinstance(e, dict))
        if extra:
            msg = f"{msg} ({extra})" if msg else extra
    return msg


def _retry_after(r: httpx.Response) -> int | None:
    if r.headers.get("retry-after", "").isdigit():
        return int(r.headers["retry-after"])
    if r.headers.get("x-ratelimit-remaining") == "0" and r.headers.get("x-ratelimit-reset", "").isdigit():
        return max(1, int(r.headers["x-ratelimit-reset"]) - int(time.time()))
    return None


def error_from_response(r: httpx.Response, what: str, *secrets: str) -> GitHubError:
    code, msg = r.status_code, redact(_gh_message(r), *secrets)
    low = msg.lower()
    limited = code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in r.headers
                                     or "rate limit" in low)
    if limited:
        ra = _retry_after(r) or 60
        return GitHubError(429, f"GitHub rate limit reached - retry in {ra}s", code, "rate_limited", ra)
    kind = {401: "auth", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "validation", 451: "legal"}.get(code, "upstream")
    detail = f": {msg}" if msg else ""
    if kind == "auth":
        return GitHubError(401, "GitHub rejected the token (401 Bad credentials) - it is invalid, expired or revoked. "
                                "Reconnect GitHub in Settings.", code, kind)
    if kind == "forbidden":
        text = f"GitHub refused to {what} (403{detail})"
        accepted, scopes = r.headers.get("x-accepted-github-permissions"), r.headers.get("x-accepted-oauth-scopes")
        if accepted:
            text += f" - the token needs: {accepted}"
        elif scopes is not None:
            text += f" - accepted scopes: {scopes or 'n/a'}; token has: {r.headers.get('x-oauth-scopes') or 'none'}"
        else:
            text += (". Check the token's permissions: classic tokens need the 'repo' scope; fine-grained tokens need "
                     "Contents and Pull requests read/write (and Administration to create repositories)")
        return GitHubError(403, text, code, kind)
    if kind == "not_found":
        return GitHubError(404, f"GitHub could not {what}: not found, or the token has no access to it (GitHub "
                                "answers 404 for private repositories it hides).", code, kind)
    if kind == "upstream":
        return GitHubError(502, f"GitHub could not {what} ({code}{detail})", code, kind)
    return GitHubError(KIND_STATUS[kind], f"GitHub could not {what} ({code}{detail})", code, kind)


# ------------------------------------------------------------------ client
class GitHub:
    """async with GitHub(token) as gh: ...   Requests are serial; GETs retry once on 5xx/network errors."""

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None, retry_delay: float = 1.0):
        self.token = token
        self.retry_delay = retry_delay
        self.rate: dict = {}
        self.client = httpx.AsyncClient(base_url=api_url(), timeout=TIMEOUT, transport=transport, follow_redirects=True,
                                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                 "X-GitHub-Api-Version": API_VERSION, "User-Agent": "ROADMAP2ARENA"})

    async def __aenter__(self) -> "GitHub":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, what: str = "complete the request", *, json: dict | None = None,
                      params: dict | None = None, etag: str | None = None) -> httpx.Response:
        """Returns 2xx/304 responses; raises GitHubError otherwise. POSTs are never retried."""
        headers = {"If-None-Match": etag} if etag else None
        attempts = 2 if method == "GET" else 1
        for i in range(attempts):
            last = i + 1 == attempts
            try:
                r = await self.client.request(method, path, json=json, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                if not last:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise GitHubError(504, f"GitHub did not answer in time (trying to {what})", None, "timeout") from exc
            except httpx.HTTPError as exc:
                if not last:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise GitHubError(502, f"Could not reach GitHub at {api_url()} ({type(exc).__name__})", None, "network") from exc
            self.rate = {k: r.headers.get(f"x-ratelimit-{k}") for k in ("limit", "remaining", "reset", "resource")
                         if r.headers.get(f"x-ratelimit-{k}") is not None} or self.rate
            if r.status_code >= 500 and not last:
                await asyncio.sleep(self.retry_delay)
                continue
            if r.status_code == 304 or r.is_success:
                return r
            raise error_from_response(r, what, self.token)
        raise AssertionError("unreachable")

    async def get_cond(self, path: str, etag: str | None, what: str, params: dict | None = None):
        """Conditional GET: (changed, json_or_None, etag). A 304 does not count against the rate limit."""
        r = await self.request("GET", path, what, params=params, etag=etag)
        if r.status_code == 304:
            return False, None, etag
        return True, r.json(), r.headers.get("etag")

    @staticmethod
    def ref(value: str) -> str:
        return quote(value, safe="/")


# ------------------------------------------------------------------ encryption
def fernet() -> Fernet | None:
    key = os.environ.get("R2A_SECRET_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except ValueError:
        logger.warning("R2A_SECRET_KEY in backend/.env is not a valid Fernet key")
        return None


def key_state() -> str:
    """ok | missing | invalid"""
    if not os.environ.get("R2A_SECRET_KEY", "").strip():
        return "missing"
    return "ok" if fernet() else "invalid"


def env_token() -> str | None:
    return os.environ.get("R2A_GITHUB_TOKEN", "").strip() or None


def _tail(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ storage
async def get_doc(db) -> dict | None:
    return await db.integrations.find_one({"_id": DOC_ID})


def _decrypt(doc: dict | None) -> tuple[str | None, str | None]:
    """(token, problem) from a stored doc; problem: None | 'key_missing' | 'undecryptable'."""
    enc = (doc or {}).get("token_enc")
    if not enc:
        return None, None
    f = fernet()
    if f is None:
        return None, "key_missing"
    try:
        return f.decrypt(enc.encode()).decode(), None
    except InvalidToken:
        return None, "undecryptable"


async def _migrate_plaintext(db, doc: dict | None) -> dict | None:
    """Encrypt a token stored in plaintext by a pre-release build (if a key is configured)."""
    if doc and doc.get("token"):
        f = fernet()
        if f:
            await db.integrations.update_one({"_id": DOC_ID}, {"$set": {"token_enc": f.encrypt(doc["token"].encode()).decode()},
                                                               "$unset": {"token": ""}})
            logger.info("migrated the stored GitHub token to encrypted storage")
            return await get_doc(db)
    return doc


async def get_token(db) -> str | None:
    """The usable token: R2A_GITHUB_TOKEN from .env wins, else the decrypted stored one."""
    env = env_token()
    if env:
        return env
    doc = await _migrate_plaintext(db, await get_doc(db))
    token, problem = _decrypt(doc)
    if problem:
        logger.warning("a GitHub token is stored but cannot be decrypted (%s)", problem)
    return token


def pending_public(doc: dict | None) -> dict | None:
    """The in-progress device flow, without the secret device_code (None if none/expired)."""
    p = (doc or {}).get("oauth_pending")
    if not p or p.get("expires_ts", 0) < time.time():
        return None
    return {"user_code": p["user_code"], "verification_uri": p["verification_uri"], "expires_at": p["expires_at"],
            "interval": p["interval"]}


TOKEN_ERRORS = {"key_missing": "A GitHub token is stored but R2A_SECRET_KEY is missing from backend/.env - restore the key "
                               "or reconnect GitHub",
                "undecryptable": "The stored GitHub token cannot be decrypted (R2A_SECRET_KEY changed?) - reconnect GitHub"}


def public(doc: dict | None) -> dict:
    """What GET /api/github returns - never the token or the device code."""
    doc = doc or {}
    auto = {**DEFAULT_AUTO_PUSH, **(doc.get("auto_push") or {})}
    cid, source = oauth_client(doc)
    base = {"auto_push": auto, "encryption": key_state(), "env_token": bool(env_token()), "token_error": None,
            "oauth": {"available": bool(cid), "client_id": cid, "client_id_source": source, "pending": pending_public(doc)}}
    empty = {"connected": False, "auth_method": None, "source": None, "username": None, "name": None, "avatar_url": None,
             "html_url": None, "scopes": [], "token_type": None, "connected_at": None}
    env = env_token()
    if env:
        u = doc.get("env_user") if (doc.get("env_user") or {}).get("tail") == _tail(env) else None
        if not u or u.get("error"):
            return {**base, **empty, "auth_method": "env", "source": "env", "token_type": token_type(env),
                    "token_error": (u or {}).get("error")}
        return {**base, "connected": True, "auth_method": "env", "source": "env", "username": u.get("username"),
                "name": u.get("name"), "avatar_url": u.get("avatar_url"), "html_url": u.get("html_url"),
                "scopes": u.get("scopes", []), "token_type": u.get("token_type"), "connected_at": u.get("checked_at")}
    if not doc.get("token_enc") and not doc.get("token"):
        return {**base, **empty}
    # a legacy plaintext token is only left unmigrated when no usable key is configured
    problem = _decrypt(doc)[1] if doc.get("token_enc") else "key_missing"
    if problem:
        return {**base, **empty, "auth_method": doc.get("auth_method") or "pat", "source": "settings",
                "token_error": TOKEN_ERRORS[problem]}
    return {**base, "connected": True, "auth_method": doc.get("auth_method") or "pat", "source": "settings",
            "username": doc.get("username"), "name": doc.get("name"), "avatar_url": doc.get("avatar_url"),
            "html_url": doc.get("html_url"), "scopes": doc.get("scopes", []), "token_type": doc.get("token_type"),
            "connected_at": doc.get("connected_at")}


async def status(db) -> dict:
    """public() after (re)validating an R2A_GITHUB_TOKEN from .env at most once per token."""
    doc = await _migrate_plaintext(db, await get_doc(db))
    env = env_token()
    if env and ((doc or {}).get("env_user") or {}).get("tail") != _tail(env):
        try:
            info = await validate_token(env)
            u = {**info, "error": None}
        except GitHubError as e:
            u = {"error": f"R2A_GITHUB_TOKEN in backend/.env: {e.message}"}
        await db.integrations.update_one({"_id": DOC_ID}, {"$set": {"env_user": {**u, "tail": _tail(env), "checked_at": now_iso()}}},
                                         upsert=True)
        doc = await get_doc(db)
    return public(doc)


def token_type(token: str) -> str:
    if token.startswith("gho_"):
        return "oauth"
    if token.startswith("github_pat_"):
        return "fine-grained"
    if token.startswith("ghp_"):
        return "classic"
    return "unknown"


async def validate_token(token: str) -> dict:
    """GET /user with the token; returns the user fields to store. Raises GitHubError."""
    async with GitHub(token) as gh:
        resp = await gh.request("GET", "/user", "validate the token")
    user = resp.json()
    scopes = [s.strip() for s in resp.headers.get("x-oauth-scopes", "").split(",") if s.strip()]
    return {"username": user.get("login"), "name": user.get("name"), "avatar_url": user.get("avatar_url"),
            "html_url": user.get("html_url"), "scopes": scopes, "token_type": token_type(token)}


def require_key() -> Fernet:
    f = fernet()
    if f is None:
        state = key_state()
        raise GitHubError(409, f"R2A_SECRET_KEY is {'not set' if state == 'missing' else 'not a valid Fernet key'}. {KEY_HINT}.")
    return f


async def connect(db, token: str, method: str = "pat", scopes: list[str] | None = None) -> dict:
    """Validate and store a token encrypted. method: "pat" | "oauth" (exclusivity is checked by the routes)."""
    f = require_key()
    info = await validate_token(token)
    if method == "oauth":
        info["token_type"] = "oauth"
        if not info["scopes"] and scopes:
            info["scopes"] = scopes
    doc = await get_doc(db) or {}
    await db.integrations.update_one({"_id": DOC_ID}, {"$set": {
        **info, "token_enc": f.encrypt(token.encode()).decode(), "auth_method": method, "connected_at": now_iso(),
        "auto_push": {**DEFAULT_AUTO_PUSH, **(doc.get("auto_push") or {})}}, "$unset": {"oauth_pending": "", "token": ""}},
        upsert=True)
    return public(await get_doc(db))


async def disconnect(db) -> dict:
    await db.integrations.update_one({"_id": DOC_ID}, {"$unset": {
        "token": "", "token_enc": "", "username": "", "name": "", "avatar_url": "", "html_url": "", "scopes": "",
        "token_type": "", "connected_at": "", "auth_method": "", "oauth_pending": ""}})
    return public(await get_doc(db))


async def set_auto_push(db, values: dict) -> dict:
    doc = await get_doc(db) or {}
    auto = {**DEFAULT_AUTO_PUSH, **(doc.get("auto_push") or {}), **values}
    await db.integrations.update_one({"_id": DOC_ID}, {"$set": {"auto_push": auto}}, upsert=True)
    return public(await get_doc(db))
