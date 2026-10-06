"""Local stand-in for the GitHub REST API (tests only - never talks to github.com).

    /app/venv/bin/python tests/github_stub.py --port 9191 --root /tmp/gh-stub

Point the backend at it with GITHUB_API_URL=http://127.0.0.1:9191 and allow local push
targets with R2A_GITHUB_ALLOW_FILE_REMOTES=1. "Repositories" are bare git repos under
--root/<owner>/<name>.git and their clone_url is that local path, so `git push` works offline.

Tokens:
  ghp_validtoken000000000000000000000000   user r2a-tester, scopes "repo, read:user"
  github_pat_finegrained0000000000000000   user r2a-tester, fine-grained (no scope header)
  ghp_readonly0000000000000000000000000000 user r2a-reader, scope "read:user" (403 on create / no push)
  anything else                            401 Bad credentials
Seeded repos: r2a-tester/existing-repo (main with an unrelated commit), r2a-tester/r2a-base
(empty, gets a base branch on demand via /_stub/seed-base), other-org/read-only (push: false).
OAuth device flow: client_id "Iv1.testclient0000" only. POST /login/device/code returns user codes
ABCD-0001, ABCD-0002, ... (interval 1 s); POST /login/oauth/access_token answers
authorization_pending until a test calls POST /_stub/device?user_code=..&action=approve|deny|expire|slow_down.
Watcher endpoints: branches/{b}, pulls/{n} (with `merged`), commits/{sha}/status, commits/{sha}/check-runs,
actions/runs?head_sha= - all with ETag / If-None-Match -> 304 and x-ratelimit-* headers.
Controls: POST /_stub/pr {full_name, number, action: merge|close}, POST /_stub/ci {sha, statuses: [state],
checks: [[status, conclusion]], runs: [[status, conclusion]], checks_forbidden: bool},
POST /_stub/fail {path, status, times, message, headers} (inject errors on a path prefix),
POST /_stub/rate {remaining}.
Debug: GET /_stub/requests (method, path, had_auth, status), POST /_stub/reset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

TOKENS = {
    "ghp_validtoken000000000000000000000000": ("r2a-tester", "repo, read:user"),
    "github_pat_finegrained0000000000000000": ("r2a-tester", None),
    "ghp_readonly0000000000000000000000000000": ("r2a-reader", "read:user"),
}
app = FastAPI()
ROOT = os.environ.get("GH_STUB_ROOT", "/tmp/gh-stub")
STATE: dict = {}
LOCK = threading.Lock()
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Someone", "GIT_AUTHOR_EMAIL": "s@example.com",
       "GIT_COMMITTER_NAME": "Someone", "GIT_COMMITTER_EMAIL": "s@example.com"}


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=check)


def _repo(owner, name, private=False, push=True, seed=False):
    path = os.path.join(ROOT, owner, f"{name}.git")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        git(None, "init", "-q", "--bare", "-b", "main", path)
        if seed:
            work = path + ".work"
            git(None, "init", "-q", "-b", "main", work)
            open(os.path.join(work, "README.md"), "w").write("existing project\n")
            git(work, "add", "-A"); git(work, "commit", "-q", "-m", "initial")
            git(work, "push", "-q", path, "main")
    full = f"{owner}/{name}"
    STATE["repos"][full] = {"name": name, "full_name": full, "owner": {"login": owner}, "private": private,
                            "default_branch": "main", "html_url": f"https://github.com/{full}",
                            "clone_url": path, "description": None, "updated_at": "2026-10-01T00:00:00Z", "size": 1,
                            "permissions": {"admin": push, "push": push, "pull": True}}
    return STATE["repos"][full]


def reset():
    with LOCK:
        subprocess.run(["rm", "-rf", ROOT])
        STATE.clear()
        STATE.update(repos={}, pulls=[], requests=[], devices={}, statuses={}, checks={}, runs={},
                     checks_forbidden=False, fails=[], rate_remaining=4999)
        _repo("r2a-tester", "existing-repo", seed=True)
        _repo("r2a-tester", "r2a-base")
        _repo("other-org", "read-only", push=False)
        for i in range(105):  # pagination
            STATE["repos"][f"r2a-tester/filler-{i:03d}"] = {**_shallow(f"filler-{i:03d}")}


def _shallow(name):
    full = f"r2a-tester/{name}"
    return {"name": name, "full_name": full, "owner": {"login": "r2a-tester"}, "private": True, "default_branch": "main",
            "html_url": f"https://github.com/{full}", "clone_url": "/nonexistent", "description": None,
            "updated_at": "2020-01-01T00:00:00Z", "size": 0,
            "permissions": {"admin": True, "push": True, "pull": True}}


def auth(request: Request):
    h = request.headers.get("authorization", "")
    STATE["requests"].append({"method": request.method, "path": request.url.path, "had_auth": h.startswith("Bearer "),
                              "api_version": request.headers.get("x-github-api-version")})
    tok = h[7:] if h.startswith("Bearer ") else ""
    return TOKENS.get(tok)


def unauth():
    return JSONResponse({"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest"}, 401)


@app.middleware("http")
async def _inject(request: Request, call_next):
    path = request.url.path
    if not path.startswith(("/_stub", "/login")):
        for f in STATE.get("fails", []):
            if path.startswith(f["path"]) and f["times"] > 0:
                f["times"] -= 1
                STATE["requests"].append({"method": request.method, "path": path, "had_auth": True, "status": f["status"]})
                return JSONResponse({"message": f.get("message") or "injected"}, f["status"], headers=f.get("headers") or {})
    resp = await call_next(request)
    if not path.startswith(("/_stub", "/login")):
        resp.headers["x-ratelimit-limit"] = "5000"
        resp.headers["x-ratelimit-remaining"] = str(STATE.get("rate_remaining", 4999))
        resp.headers["x-ratelimit-reset"] = str(int(time.time()) + 3600)
        resp.headers["x-ratelimit-resource"] = "core"
        if STATE["requests"] and STATE["requests"][-1]["path"] == path:
            STATE["requests"][-1].setdefault("status", resp.status_code)
    return resp


def conditional(request: Request, data) -> Response:
    """JSON with an ETag; 304 when If-None-Match matches (like GitHub's conditional requests)."""
    body = json.dumps(data, sort_keys=True)
    etag = '"' + hashlib.sha1(body.encode()).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(body, media_type="application/json", headers={"ETag": etag})


@app.post("/_stub/fail")
async def _fail(request: Request):
    f = await request.json()
    STATE["fails"].append({"path": f["path"], "status": int(f.get("status", 500)), "times": int(f.get("times", 1)),
                           "message": f.get("message"), "headers": f.get("headers")})
    return {"ok": True}


@app.post("/_stub/rate")
def _rate(remaining: int):
    STATE["rate_remaining"] = remaining
    return {"ok": True}


@app.post("/_stub/pr")
async def _pr(request: Request):
    b = await request.json()
    for p in STATE["pulls"]:
        if p["repo"] == b["full_name"] and p["number"] == int(b["number"]):
            p["state"], p["merged"] = "closed", b["action"] == "merge"
            return {"ok": True}
    return JSONResponse({"error": "unknown pr"}, 404)


@app.post("/_stub/ci")
async def _ci(request: Request):
    b = await request.json()
    sha = b.get("sha")
    if "statuses" in b:
        STATE["statuses"][sha] = list(b["statuses"])
    if "checks" in b:
        STATE["checks"][sha] = [list(x) for x in b["checks"]]
    if "runs" in b:
        STATE["runs"][sha] = [list(x) for x in b["runs"]]
    if "checks_forbidden" in b:
        STATE["checks_forbidden"] = bool(b["checks_forbidden"])
    return {"ok": True}


@app.post("/_stub/reset")
def _reset():
    reset()
    return {"ok": True}


@app.get("/_stub/requests")
def _requests():
    return STATE["requests"]


@app.post("/_stub/seed-base")
def _seed_base(full_name: str, from_bundle: str):
    """Give a stub repo a main branch that shares history with a job (from a git bundle)."""
    path = STATE["repos"][full_name]["clone_url"]
    git(path, "fetch", "-q", from_bundle, "main:main")
    return {"ok": True}


CLIENT_ID = "Iv1.testclient0000"


async def _form(request: Request) -> dict:
    """Parse an application/x-www-form-urlencoded body (no python-multipart needed)."""
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}


@app.post("/login/device/code")
async def device_code(request: Request):
    form = await _form(request)
    STATE["requests"].append({"method": "POST", "path": "/login/device/code", "had_auth": False})
    if form.get("client_id") != CLIENT_ID:
        return JSONResponse({"error": "Not Found"}, 404)
    n = len(STATE["devices"]) + 1
    code = f"ABCD-{n:04d}"
    STATE["devices"][f"dc-{n}"] = {"user_code": code, "state": "pending", "scope": form.get("scope", "")}
    return {"device_code": f"dc-{n}", "user_code": code, "verification_uri": "https://github.com/login/device",
            "expires_in": 900, "interval": 1}


@app.post("/login/oauth/access_token")
async def access_token(request: Request):
    form = await _form(request)
    STATE["requests"].append({"method": "POST", "path": "/login/oauth/access_token", "had_auth": False})
    if form.get("client_id") != CLIENT_ID:
        return {"error": "incorrect_client_credentials"}
    if form.get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code":
        return {"error": "unsupported_grant_type"}
    d = STATE["devices"].get(form.get("device_code", ""))
    if not d:
        return {"error": "incorrect_device_code"}
    state = d["state"]
    if state == "approved":
        d["state"] = "used"
        return {"access_token": "ghp_validtoken000000000000000000000000", "token_type": "bearer", "scope": "repo"}
    if state == "slow_down":
        d["state"] = "pending"
        return {"error": "slow_down", "interval": 2}
    if state == "used":
        return {"error": "incorrect_device_code"}
    return {"error": {"pending": "authorization_pending", "denied": "access_denied", "expired": "expired_token"}[state]}


@app.post("/_stub/device")
def _device(user_code: str, action: str):
    for d in STATE["devices"].values():
        if d["user_code"] == user_code:
            d["state"] = {"approve": "approved", "deny": "denied", "expire": "expired", "slow_down": "slow_down"}[action]
            return {"ok": True}
    return JSONResponse({"error": "unknown code"}, 404)


@app.get("/user")
def user(request: Request):
    t = auth(request)
    if not t:
        return unauth()
    headers = {"X-OAuth-Scopes": t[1]} if t[1] is not None else {}
    return JSONResponse({"login": t[0], "name": t[0].title(), "avatar_url": "https://avatars.example/u.png",
                         "html_url": f"https://github.com/{t[0]}"}, headers=headers)


@app.get("/user/repos")
def user_repos(request: Request, page: int = 1, per_page: int = 30):
    t = auth(request)
    if not t:
        return unauth()
    mine = [r for r in STATE["repos"].values() if t[0] == "r2a-tester" or r["owner"]["login"] == t[0]]
    mine.sort(key=lambda r: r["updated_at"], reverse=True)
    return mine[(page - 1) * per_page: page * per_page]


@app.post("/user/repos")
async def create_repo(request: Request):
    t = auth(request)
    if not t:
        return unauth()
    if t[1] is not None and "repo" not in [s.strip() for s in t[1].split(",")]:
        return JSONResponse({"message": "Resource not accessible by personal access token"}, 403)
    body = await request.json()
    full = f"{t[0]}/{body['name']}"
    with LOCK:
        if full in STATE["repos"]:
            return JSONResponse({"message": "Repository creation failed.",
                                 "errors": [{"resource": "Repository", "code": "custom", "field": "name",
                                             "message": "name already exists on this account"}]}, 422)
        repo = _repo(t[0], body["name"], private=bool(body.get("private")))
        repo["description"] = body.get("description")
    return JSONResponse(repo, 201)


@app.get("/repos/{owner}/{name}")
def get_repo(owner: str, name: str, request: Request):
    t = auth(request)
    if not t:
        return unauth()
    r = STATE["repos"].get(f"{owner}/{name}")
    if not r:
        return JSONResponse({"message": "Not Found"}, 404)
    return r


@app.get("/repos/{owner}/{name}/branches/{branch:path}")
def get_branch(owner: str, name: str, branch: str, request: Request):
    if not auth(request):
        return unauth()
    r = STATE["repos"].get(f"{owner}/{name}")
    sha = git(r["clone_url"], "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).stdout.strip() if r else ""
    if not sha:
        return JSONResponse({"message": "Branch not found"}, 404)
    return conditional(request, {"name": branch, "commit": {"sha": sha}, "protected": False})


@app.get("/repos/{owner}/{name}/pulls/{number}")
def get_pull(owner: str, name: str, number: int, request: Request):
    if not auth(request):
        return unauth()
    for p in STATE["pulls"]:
        if p["repo"] == f"{owner}/{name}" and p["number"] == number:
            return conditional(request, p)
    return JSONResponse({"message": "Not Found"}, 404)


@app.get("/repos/{owner}/{name}/commits/{sha}/status")
def combined_status(owner: str, name: str, sha: str, request: Request):
    if not auth(request):
        return unauth()
    sts = STATE["statuses"].get(sha, [])
    state = "failure" if any(x in ("failure", "error") for x in sts) else "pending" if not sts or "pending" in sts else "success"
    return conditional(request, {"state": state, "total_count": len(sts), "sha": sha,
                                 "statuses": [{"state": x, "context": f"ci/{i}"} for i, x in enumerate(sts)]})


@app.get("/repos/{owner}/{name}/commits/{sha}/check-runs")
def check_runs(owner: str, name: str, sha: str, request: Request):
    if not auth(request):
        return unauth()
    if STATE["checks_forbidden"]:
        return JSONResponse({"message": "Resource not accessible by personal access token"}, 403,
                            headers={"X-Accepted-GitHub-Permissions": "checks=read"})
    runs = STATE["checks"].get(sha, [])
    return conditional(request, {"total_count": len(runs), "check_runs": [
        {"name": f"check-{i}", "status": st, "conclusion": c} for i, (st, c) in enumerate(runs)]})


@app.get("/repos/{owner}/{name}/actions/runs")
def workflow_runs(owner: str, name: str, request: Request, head_sha: str = ""):
    if not auth(request):
        return unauth()
    runs = STATE["runs"].get(head_sha, [])
    return conditional(request, {"total_count": len(runs), "workflow_runs": [
        {"name": f"wf-{i}", "status": st, "conclusion": c, "head_sha": head_sha} for i, (st, c) in enumerate(runs)]})


@app.get("/repos/{owner}/{name}/pulls")
def list_pulls(owner: str, name: str, request: Request, head: str = "", state: str = "open"):
    if not auth(request):
        return unauth()
    full = f"{owner}/{name}"
    return [p for p in STATE["pulls"] if p["repo"] == full and (not head or f"{owner}:{p['head']['ref']}" == head)]


@app.post("/repos/{owner}/{name}/pulls")
async def create_pull(owner: str, name: str, request: Request):
    if not auth(request):
        return unauth()
    full = f"{owner}/{name}"
    r = STATE["repos"].get(full)
    if not r:
        return JSONResponse({"message": "Not Found"}, 404)
    body = await request.json()
    path = r["clone_url"]
    def ref(b):
        return git(path, "rev-parse", "--verify", "-q", f"refs/heads/{b}", check=False).stdout.strip()
    head_sha, base_sha = ref(body["head"]), ref(body["base"])
    err = lambda m: JSONResponse({"message": "Validation Failed", "errors": [{"resource": "PullRequest", "code": "custom", "message": m}]}, 422)
    if not base_sha:
        return JSONResponse({"message": "Validation Failed", "errors": [{"resource": "PullRequest", "field": "base", "code": "invalid"}]}, 422)
    if not head_sha:
        return JSONResponse({"message": "Validation Failed", "errors": [{"resource": "PullRequest", "field": "head", "code": "invalid"}]}, 422)
    if any(p["repo"] == full and p["head"]["ref"] == body["head"] for p in STATE["pulls"]):
        return err(f"A pull request already exists for {owner}:{body['head']}.")
    if git(path, "merge-base", head_sha, base_sha, check=False).returncode != 0:
        return err(f"The {body['head']} branch has no history in common with {body['base']}")
    if head_sha == base_sha:
        return err(f"No commits between {body['base']} and {body['head']}")
    n = len(STATE["pulls"]) + 1
    pr = {"number": n, "html_url": f"https://github.com/{full}/pull/{n}", "repo": full, "title": body.get("title"),
          "body": body.get("body"), "head": {"ref": body["head"]}, "base": {"ref": body["base"]}, "state": "open", "merged": False}
    STATE["pulls"].append(pr)
    return JSONResponse(pr, 201)


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9191)
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args()
    ROOT = a.root
    reset()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
