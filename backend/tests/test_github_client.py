"""Unit tests for the GitHub client, error mapping, watcher logic and git push - mocked with respx
and local bare repos. No server, no network, never github.com.

    ./venv/bin/python backend/tests/test_github_client.py (from the repository root)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

# Importing the client also imports orchestrator/settings. These are inert defaults,
# not a running MongoDB or gateway; keep this mocked suite runnable without .env.
for key, value in {
    "MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "roadmap2arena_github_client_tests",
    "ARENA2API_URL": "http://127.0.0.1:9090", "ARENA2API_MODEL": "gpt-4o",
    "ARENA_STEP_DELAY_SECONDS": "0.2", "ARENA_REQUEST_TIMEOUT_SECONDS": "300", "CORS_ORIGINS": "*",
}.items():
    os.environ.setdefault(key, value)
os.environ["ARENA2API_API_KEY"] = ""
os.environ["GITHUB_API_URL"] = "https://api.github.test"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
import respx  # noqa: E402

import github_client as gc  # noqa: E402
import github_integration as gi  # noqa: E402
from isolated_server import run_tests  # noqa: E402

API = "https://api.github.test"
TOKEN = "ghp_unitTestToken0000000000000000000000"
TMP = tempfile.mkdtemp(prefix="r2a-ghc-")


def run(coro):
    return asyncio.run(coro)


async def call(method, path, **kw):
    async with gc.GitHub(TOKEN, retry_delay=0) as gh:
        return await gh.request(method, path, "do the thing", **kw)


async def error(method, path, **kw):
    try:
        await call(method, path, **kw)
    except gc.GitHubError as e:
        return e
    raise AssertionError("expected GitHubError")


@respx.mock
def test_headers():
    route = respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"login": "u"}))
    run(call("GET", "/user"))
    h = route.calls.last.request.headers
    assert h["authorization"] == f"Bearer {TOKEN}" and h["accept"] == "application/vnd.github+json"
    assert h["x-github-api-version"] == "2026-03-10" and h["user-agent"] == "ROADMAP2ARENA"
    assert gc.TIMEOUT.read == 20.0 and gc.TIMEOUT.connect == 10.0


@respx.mock
def test_get_retries_once_post_never():
    r = respx.get(f"{API}/x").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    assert run(call("GET", "/x")).status_code == 200 and r.call_count == 2
    r2 = respx.get(f"{API}/y").mock(side_effect=[httpx.Response(500), httpx.Response(500), httpx.Response(200)])
    e = run(error("GET", "/y"))
    assert e.status == 502 and r2.call_count == 2
    p = respx.post(f"{API}/z").mock(return_value=httpx.Response(502))
    assert run(error("POST", "/z")).status == 502 and p.call_count == 1
    n = respx.get(f"{API}/net").mock(side_effect=httpx.ConnectError("boom"))
    e = run(error("GET", "/net"))
    assert e.status == 502 and e.kind == "network" and n.call_count == 2
    respx.get(f"{API}/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    assert run(error("GET", "/slow")).status == 504
    pt = respx.post(f"{API}/slowpost").mock(side_effect=httpx.ReadTimeout("slow"))
    assert run(error("POST", "/slowpost")).status == 504 and pt.call_count == 1


@respx.mock
def test_error_mapping():
    cases = [
        (httpx.Response(401, json={"message": "Bad credentials"}), 401, "Reconnect GitHub"),
        (httpx.Response(403, json={"message": "Resource not accessible"}, headers={"X-Accepted-GitHub-Permissions": "contents=write"}), 403, "the token needs: contents=write"),
        (httpx.Response(403, json={"message": "nope"}, headers={"X-Accepted-OAuth-Scopes": "repo", "X-OAuth-Scopes": "read:user"}), 403, "token has: read:user"),
        (httpx.Response(403, json={"message": "API rate limit exceeded"}, headers={"x-ratelimit-remaining": "0", "retry-after": "42"}), 429, "retry in 42s"),
        (httpx.Response(429, json={"message": "secondary rate limit"}, headers={"retry-after": "7"}), 429, "retry in 7s"),
        (httpx.Response(404, json={"message": "Not Found"}), 404, "no access"),
        (httpx.Response(409, json={"message": "Git Repository is empty."}), 409, "empty"),
        (httpx.Response(422, json={"message": "Validation Failed", "errors": [{"message": "already exists"}]}), 422, "already exists"),
        (httpx.Response(451, json={"message": "blocked"}), 451, "blocked"),
        (httpx.Response(400, json={"message": "weird"}), 502, "weird"),
    ]
    for i, (resp, status, text) in enumerate(cases):
        respx.get(f"{API}/e{i}").mock(return_value=resp)
        e = run(error("GET", f"/e{i}"))
        assert e.status == status and text in e.message, (i, e.status, e.message)
    respx.get(f"{API}/rl").mock(return_value=httpx.Response(403, json={"message": "API rate limit exceeded"}, headers={"retry-after": "30"}))
    e = run(error("GET", "/rl"))
    h = gi._http_error(e)
    assert h.status_code == 429 and h.headers == {"Retry-After": "30"}


@respx.mock
def test_token_redacted_from_errors():
    respx.get(f"{API}/leak").mock(return_value=httpx.Response(422, json={"message": f"bad token {TOKEN} and github_pat_11ABCDEFGHIJKLMNOPQRSTUV_x"}))
    e = run(error("GET", "/leak"))
    assert TOKEN not in e.message and "github_pat_" not in e.message and "***" in e.message


@respx.mock
def test_conditional_304():
    route = respx.get(f"{API}/c").mock(side_effect=[httpx.Response(200, json={"a": 1}, headers={"etag": '"v1"'}), httpx.Response(304)])

    async def go():
        async with gc.GitHub(TOKEN, retry_delay=0) as gh:
            first = await gh.get_cond("/c", None, "read")
            second = await gh.get_cond("/c", first[2], "read")
            return first, second, gh.rate
    first, second, _ = run(go())
    assert first == (True, {"a": 1}, '"v1"') and second == (False, None, '"v1"')
    assert route.calls.last.request.headers["if-none-match"] == '"v1"'


def watch(**kw):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return {"job_id": "j", "full_name": "octo/r", "branch": "feat", "html_url": "https://github.com/octo/r", "head_sha": "a" * 40,
            "pr_number": 7, "etags": {}, "state": {"pr_state": "open"}, "pushed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(), **kw}


@respx.mock
def test_poll_pr_merged_and_checks_fallback_to_actions():
    sha = "a" * 40
    respx.get(f"{API}/repos/octo/r/branches/feat").mock(return_value=httpx.Response(200, json={"commit": {"sha": sha}}, headers={"etag": '"b"'}))
    respx.get(f"{API}/repos/octo/r/pulls/7").mock(return_value=httpx.Response(200, json={
        "state": "closed", "merged": True, "html_url": "https://github.com/octo/r/pull/7"}))  # no merge_commit_sha needed
    respx.get(f"{API}/repos/octo/r/commits/{sha}/status").mock(return_value=httpx.Response(200, json={"state": "pending", "total_count": 0}))
    respx.get(f"{API}/repos/octo/r/commits/{sha}/check-runs").mock(return_value=httpx.Response(403, json={"message": "Resource not accessible"}))
    runs = respx.get(f"{API}/repos/octo/r/actions/runs").mock(return_value=httpx.Response(200, json={"workflow_runs": [{"status": "completed", "conclusion": "failure"}]}))
    w = watch()

    async def go():
        async with gc.GitHub(TOKEN, retry_delay=0) as gh:
            return await gi.poll_watch(gh, w)
    events = run(go())
    assert [e[0] for e in events] == ["github_pr_merged", "github_checks_failed"], events
    assert runs.calls.last.request.url.params["head_sha"] == sha
    assert w["state"]["pr_state"] == "merged" and w["state"]["ci"] == "failure" and w["state"]["checks_api"] == "unavailable"
    assert w["active"] is False and w["etags"]["branch"] == '"b"'


@respx.mock
def test_poll_external_push_and_quiet_when_unchanged():
    new = "b" * 40
    respx.get(f"{API}/repos/octo/r/branches/feat").mock(side_effect=[
        httpx.Response(200, json={"commit": {"sha": new}}, headers={"etag": '"b2"'}), httpx.Response(304)])
    respx.get(f"{API}/repos/octo/r/pulls/7").mock(side_effect=[httpx.Response(200, json={"state": "open", "merged": False, "html_url": "u"}, headers={"etag": '"p"'}), httpx.Response(304)])
    respx.get(f"{API}/repos/octo/r/commits/{new}/status").mock(side_effect=[httpx.Response(200, json={"state": "pending", "total_count": 0}, headers={"etag": '"s"'}), httpx.Response(304)])
    respx.get(f"{API}/repos/octo/r/commits/{new}/check-runs").mock(side_effect=[
        httpx.Response(200, json={"check_runs": [{"status": "in_progress", "conclusion": None}]}, headers={"etag": '"c"'}), httpx.Response(304)])
    w = watch()

    async def go():
        async with gc.GitHub(TOKEN, retry_delay=0) as gh:
            return await gi.poll_watch(gh, w), await gi.poll_watch(gh, w)
    first, second = run(go())
    assert [e[0] for e in first] == ["github_pushed"] and new[:7] in first[0][1] and second == []
    assert w["head_sha"] == new and w["state"]["ci"] == "pending" and w["active"] is True


def git(cwd, *a, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, capture_output=True, text=True, check=check)


def test_push_to_local_bare_repo_and_non_fast_forward():
    bare = os.path.join(TMP, "remote.git")
    git(TMP, "init", "-q", "--bare", "-b", "main", bare)
    a, b = os.path.join(TMP, "a"), os.path.join(TMP, "b")
    for d, msg in ((a, "one"), (b, "other")):
        git(TMP, "init", "-q", "-b", "main", d)
        open(os.path.join(d, "f.txt"), "w").write(msg)
        git(d, "add", "-A"); git(d, "commit", "-q", "-m", msg)
    gi.push_sync(a, bare, "r2a/x", TOKEN, "u", timeout=30)
    head = git(a, "rev-parse", "HEAD").stdout.strip()
    assert git(bare, "rev-parse", "refs/heads/r2a/x").stdout.strip() == head
    try:
        gi.push_sync(b, bare, "r2a/x", TOKEN, "u", timeout=30)
        raise AssertionError("non-fast-forward push must fail")
    except gc.GitHubError as e:
        assert e.status == 409 and "never force-pushes" in e.message and TOKEN not in e.message
    assert git(bare, "rev-parse", "refs/heads/r2a/x").stdout.strip() == head  # unchanged
    assert TOKEN not in open(os.path.join(a, ".git", "config")).read()


def test_redact_and_classify():
    assert gc.redact("Authorization: Basic dTp0b2s= https://x:y@github.com/a ghs_abcdefghijklmnopqrstuvwx") == "Authorization: Basic *** https://***@github.com/a ***"
    for text, kind in (("! [rejected] main -> main (fetch first)", "non_fast_forward"), ("fatal: Authentication failed for", "auth"),
                       ("remote: Repository not found.", "not_found"), ("remote: error: GH006: Protected branch update failed", "rules"),
                       ("fatal: unable to access: Operation timed out", "timeout"), ("something else", "failed")):
        assert gi.classify_push(text) == kind, text


if __name__ == "__main__":
    code = run_tests(globals())
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
