"""PR D: GitHub integration against a LOCAL GitHub API stub (tests/github_stub.py).

Never contacts github.com: the backend gets GITHUB_API_URL=<stub> and pushes go to local
bare repositories (R2A_GITHUB_ALLOW_FILE_REMOTES=1).

    /app/venv/bin/python tests/test_github.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from isolated_server import IsolatedServer, free_port, run_tests  # noqa: E402

TOKEN = "ghp_validtoken000000000000000000000000"
FINE = "github_pat_finegrained0000000000000000"
READONLY = "ghp_readonly0000000000000000000000000000"
STUB_URL = os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090")
RM = "# Push Demo App\n\n### First step\nmake a\n\n### Second step\nmake b\n"
TMP = tempfile.mkdtemp(prefix="r2a-gh-test-")
GH_ROOT = os.path.join(TMP, "gh")
GH_PORT = free_port()
GH = f"http://127.0.0.1:{GH_PORT}"
KEY = Fernet.generate_key().decode()
FERNET = Fernet(KEY.encode())
GH_ENV = {"GITHUB_API_URL": GH, "GITHUB_OAUTH_URL": GH, "GITHUB_OAUTH_CLIENT_ID": "", "R2A_GITHUB_TOKEN": "",
          "R2A_GITHUB_ALLOW_FILE_REMOTES": "1"}
SRV = IsolatedServer(extra_env={"R2A_DATA_DIR": os.path.join(TMP, "data"), "R2A_SECRET_KEY": KEY, **GH_ENV})
REVOKED = "ghp_revoked000000000000000000000000000"
CLIENT_ID = "Iv1.testclient0000"
BASE = ""
C = httpx.Client(timeout=60)
STATE = {}
GENV = {"PATH": os.environ["PATH"], "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, env=GENV)


def bare(full):
    return os.path.join(GH_ROOT, f"{full}.git")


def create_job(model="gpt-4o", roadmap=RM):
    r = C.post(f"{BASE}/jobs", json={"roadmap_md": roadmap, "model": model, "arena_url": STUB_URL})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def wait(job_id, statuses=("done", "error", "stopped"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = C.get(f"{BASE}/jobs/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.25)
    raise AssertionError(f"{job_id} still {j['status']}")


def push(job_id, **body):
    return C.post(f"{BASE}/jobs/{job_id}/github/push", json=body)


def set_stored_token(token, key=None):
    """Write a token into Mongo the way the backend stores it (Fernet-encrypted)."""
    f = Fernet(key) if key else FERNET
    SRV.db().integrations.update_one({"_id": "github"}, {"$set": {"token_enc": f.encrypt(token.encode()).decode()}})


def notifs(event=None, job=None):
    q = {k: v for k, v in (("event", event), ("job_id", job)) if v}
    return list(SRV.db().notifications.find(q, {"_id": 0}).sort("created_at", 1))


def stubctl(endpoint, **body):
    r = httpx.post(f"{GH}/_stub/{endpoint}", json=body)
    assert r.status_code == 200, r.text


def wait_for(fn, timeout=15, step=0.3):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition never met")


def logs(job_id):
    return [l["msg"] for l in C.get(f"{BASE}/jobs/{job_id}").json()["log"]]


def test_not_connected_errors():
    g = C.get(f"{BASE}/github").json()
    assert g["connected"] is False and g["username"] is None and "token" not in g
    assert g["auto_push"] == {"enabled": False, "private": True}
    j = create_job()
    wait(j)
    STATE["job"] = j
    r = push(j)
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    assert C.get(f"{BASE}/github/repos").status_code == 409
    info = C.get(f"{BASE}/jobs/{j}/github").json()
    assert info["connected"] is False and info["defaults"]["branch"] == "r2a/push-demo-app"
    assert info["defaults"]["repo_name"] == "push-demo-app" and info["defaults"]["pr_title"] == "Push Demo App"
    assert "- [x] Step 1: First step" in info["defaults"]["pr_body"] and info["last_push"] is None


def test_connect_validation():
    assert C.put(f"{BASE}/github/token", json={"token": "short"}).status_code == 422
    assert C.put(f"{BASE}/github/token", json={"token": "has spaces in it 0123456789"}).status_code == 422
    r = C.put(f"{BASE}/github/token", json={"token": REVOKED})
    assert r.status_code == 401 and "Bad credentials" in r.json()["detail"], r.text
    assert C.get(f"{BASE}/github").json()["connected"] is False
    r = C.put(f"{BASE}/github/token", json={"token": FINE})
    assert r.status_code == 200 and r.json()["token_type"] == "fine-grained" and r.json()["scopes"] == []
    r = C.put(f"{BASE}/github/token", json={"token": f"  {TOKEN}\n"})
    g = r.json()
    assert r.status_code == 200 and g["connected"] and g["username"] == "r2a-tester"
    assert g["scopes"] == ["repo", "read:user"] and g["token_type"] == "classic" and g["connected_at"]
    # the token is never returned by any endpoint
    for path in ("/github", f"/jobs/{STATE['job']}/github", f"/jobs/{STATE['job']}", "/settings"):
        assert TOKEN not in C.get(f"{BASE}{path}").text, path
    doc = SRV.db().integrations.find_one({"_id": "github"})
    assert "token" not in doc and TOKEN not in str(doc)  # encrypted at rest (Fernet, R2A_SECRET_KEY)
    assert FERNET.decrypt(doc["token_enc"].encode()).decode() == TOKEN
    assert g["encryption"] == "ok" and g["source"] == "settings" and g["token_error"] is None
    reqs = httpx.get(f"{GH}/_stub/requests").json()
    assert reqs[-1]["path"] == "/user" and reqs[-1]["had_auth"] and reqs[-1]["api_version"] == "2026-03-10"


def test_error_mapping_and_retries():
    """httpx client: GET retries once on 5xx, POST never; 403/429/451/rate-limit mapping."""
    stubctl("fail", path="/user/repos", status=503, times=1)
    r = C.get(f"{BASE}/github/repos")
    assert r.status_code == 200, r.text  # one retry absorbed the 503
    stubctl("fail", path="/user/repos", status=500, times=2)
    r = C.get(f"{BASE}/github/repos")
    assert r.status_code == 502 and "(500" in r.json()["detail"]
    stubctl("fail", path="/user/repos", status=403, times=1, message="API rate limit exceeded",
         headers={"x-ratelimit-remaining": "0", "retry-after": "30"})
    r = C.get(f"{BASE}/github/repos")
    assert r.status_code == 429 and r.headers["retry-after"] == "30" and "retry in 30s" in r.json()["detail"]
    stubctl("fail", path="/user/repos", status=403, times=1, message="Resource not accessible by personal access token",
         headers={"X-Accepted-GitHub-Permissions": "metadata=read"})
    r = C.get(f"{BASE}/github/repos")
    assert r.status_code == 403 and "the token needs: metadata=read" in r.json()["detail"]
    stubctl("fail", path="/user/repos", status=451, times=1, message="Repository access blocked")
    assert C.get(f"{BASE}/github/repos").status_code == 451
    j = STATE["job"]
    n0 = len([x for x in httpx.get(f"{GH}/_stub/requests").json() if x["path"] == "/user/repos" and x["method"] == "POST"])
    stubctl("fail", path="/user/repos", status=502, times=1)
    r = push(j, mode="new", repo_name="never-retried")
    n1 = len([x for x in httpx.get(f"{GH}/_stub/requests").json() if x["path"] == "/user/repos" and x["method"] == "POST"])
    assert r.status_code == 502 and n1 - n0 == 1, (r.text, n0, n1)  # POSTs are never retried
    httpx.post(f"{GH}/_stub/fail", json={"path": "/user/repos", "status": 500, "times": 0})


def test_list_repos_paginated_and_search():
    r = C.get(f"{BASE}/github/repos").json()
    assert r["total"] >= 108 and not r["truncated"]  # 105 fillers + 3 seeded, two pages
    names = {x["full_name"]: x for x in r["items"]}
    assert names["other-org/read-only"]["can_push"] is False and names["r2a-tester/existing-repo"]["can_push"]
    assert names["r2a-tester/existing-repo"]["default_branch"] == "main"
    r = C.get(f"{BASE}/github/repos?q=EXISTING").json()
    assert [x["full_name"] for x in r["items"]] == ["r2a-tester/existing-repo"]


def test_push_new_repo():
    j = STATE["job"]
    assert push(j, mode="new", open_pr=True).status_code == 422  # no base in a new repo
    assert push(j, mode="new", repo_name="bad name!").status_code == 422
    assert push(j, mode="new", repo_name="x.git").status_code == 422
    assert push(j, mode="new", branch="bad..branch").status_code == 422
    assert push(j, mode="new", branch="-x").status_code == 422
    r = push(j, mode="new", private=True)
    assert r.status_code == 200, r.text
    res = r.json()
    head = C.get(f"{BASE}/jobs/{j}/git").json()["head"]
    assert res["pushed"] and res["branch"] == "r2a/push-demo-app" and res["head"] == head
    STATE["head"] = head
    assert res["repo"] == {"full_name": "r2a-tester/push-demo-app", "html_url": "https://github.com/r2a-tester/push-demo-app",
                           "private": True, "created": True}
    assert res["branch_url"] == "https://github.com/r2a-tester/push-demo-app/tree/r2a/push-demo-app"
    assert res["commit_url"].endswith(head) and res["pr"] is None and res["commit_count"] == 2
    b = bare("r2a-tester/push-demo-app")
    assert git(b, "rev-parse", "refs/heads/r2a/push-demo-app").stdout.strip() == head
    assert git(b, "log", "--format=%s", "r2a/push-demo-app").stdout.split("\n")[:2] == ["Step 2: Second step", "Step 1: First step"]
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g["remotes"][0]["full_name"] == "r2a-tester/push-demo-app" and g["remotes"][0]["pushed_head"] == head
    assert "clone_url" in g["remotes"][0] and TOKEN not in str(g)
    assert C.get(f"{BASE}/jobs/{j}/github").json()["last_push"]["repo"]["full_name"] == "r2a-tester/push-demo-app"
    assert any("GitHub: pushed 2 commits to r2a-tester/push-demo-app branch r2a/push-demo-app (new repository)" in m for m in logs(j))
    # same name again -> clear conflict
    r = push(j, mode="new")
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


def test_push_existing_same_branch_is_idempotent_and_fast_forward():
    j = STATE["job"]
    r = push(j, mode="existing", repo_full_name="r2a-tester/push-demo-app")
    assert r.status_code == 200 and r.json()["repo"]["created"] is False
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    assert len([x for x in g["remotes"] if x["full_name"] == "r2a-tester/push-demo-app"]) == 1  # upserted


def test_conflict_never_force_pushes():
    j2 = create_job(roadmap="# Other\n\n### Only\nx\n")
    wait(j2)
    STATE["job2"] = j2
    before = git(bare("r2a-tester/push-demo-app"), "rev-parse", "refs/heads/r2a/push-demo-app").stdout
    r = push(j2, mode="existing", repo_full_name="r2a-tester/push-demo-app", branch="r2a/push-demo-app")
    assert r.status_code == 409 and "never force-pushes" in r.json()["detail"], r.text
    assert git(bare("r2a-tester/push-demo-app"), "rev-parse", "refs/heads/r2a/push-demo-app").stdout == before
    assert any("GitHub: push failed - The branch" in m for m in logs(j2))


def test_existing_repo_errors():
    j = STATE["job"]
    assert push(j, mode="existing").status_code == 422
    assert push(j, mode="existing", repo_full_name="no-slash").status_code == 422
    r = push(j, mode="existing", repo_full_name="r2a-tester/missing")
    assert r.status_code == 404 and "not found" in r.json()["detail"]
    r = push(j, mode="existing", repo_full_name="other-org/read-only")
    assert r.status_code == 403 and "no push access" in r.json()["detail"]


def test_pr_unrelated_history_reported_but_pushed():
    j = STATE["job"]
    r = push(j, mode="existing", repo_full_name="r2a-tester/existing-repo", branch="r2a/try", open_pr=True)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["pushed"] and res["pr"] is None and "no history in common" in res["pr_error"]
    assert git(bare("r2a-tester/existing-repo"), "rev-parse", "refs/heads/main").returncode == 0
    assert any(m.startswith("GitHub: Pushed, but no PR") for m in logs(j))


def test_pr_opened_and_existing_pr_reused():
    """A repo whose main shares the job's first commit: PR from the job branch opens."""
    j = STATE["job"]
    d = C.get(f"{BASE}/jobs/{j}/git").json()
    first = d["commits"][-1]["sha"]
    bundle = os.path.join(TMP, "b.bundle")
    open(bundle, "wb").write(C.get(f"{BASE}/jobs/{j}/git/download?format=bundle").content)
    tmp = os.path.join(TMP, "base-src")
    git(TMP, "clone", "-q", bundle, tmp)
    git(tmp, "checkout", "-q", "--detach")
    git(tmp, "branch", "-f", "main", first)
    b2 = os.path.join(TMP, "base.bundle")
    git(tmp, "bundle", "create", b2, "main")
    assert httpx.post(f"{GH}/_stub/seed-base", params={"full_name": "r2a-tester/r2a-base", "from_bundle": b2}).status_code == 200
    r = push(j, mode="existing", repo_full_name="r2a-tester/r2a-base", branch="feature/x", open_pr=True,
             pr_title="My PR", pr_body="Body")
    res = r.json()
    assert r.status_code == 200 and res["pr"] == {"number": res["pr"]["number"], "html_url": f"https://github.com/r2a-tester/r2a-base/pull/{res['pr']['number']}",
                                                  "existing": False, "base": "main", "state": "open"}, res
    STATE["pr"] = res["pr"]["number"]
    r = push(j, mode="existing", repo_full_name="r2a-tester/r2a-base", branch="feature/x", open_pr=True)
    res2 = r.json()
    assert res2["pr"]["existing"] is True and res2["pr"]["number"] == res["pr"]["number"]
    r = push(j, mode="existing", repo_full_name="r2a-tester/r2a-base", branch="feature/y", open_pr=True, pr_base="nope")
    assert "base branch 'nope' does not exist" in r.json()["pr_error"]
    r = push(j, mode="existing", repo_full_name="r2a-tester/r2a-base", branch="main2", open_pr=True, pr_base="main2")
    assert "nothing to compare" in r.json()["pr_error"]
    assert any("GitHub: pull request #" in m for m in logs(j))


def test_push_notifications_and_watches():
    """Each push emits github_pushed (+ github_pr_opened for a new PR) and upserts a watch."""
    j = STATE["job"]
    pushed = notifs("github_pushed", j)
    assert pushed and pushed[0]["link"] == "https://github.com/r2a-tester/push-demo-app/tree/r2a/push-demo-app"
    assert pushed[0]["message"].startswith("Pushed 2 commits to r2a-tester/push-demo-app:r2a/push-demo-app")
    assert pushed[0]["url"].endswith(f"?tab=history&job={j}")
    opened = notifs("github_pr_opened", j)
    assert len(opened) == 1 and opened[0]["link"].endswith(f"/r2a-base/pull/{STATE['pr']}")  # reused PR: no 2nd event
    listed = C.get(f"{BASE}/notifications").json()
    assert any(n.get("link") for n in listed["items"]), "notification API exposes link"
    w = C.get(f"{BASE}/github/watches").json()
    assert w["poll_seconds"] == 60
    keys = {(x["full_name"], x["branch"]) for x in w["items"]}
    assert ("r2a-tester/push-demo-app", "r2a/push-demo-app") in keys and ("r2a-tester/r2a-base", "feature/x") in keys
    fx = next(x for x in w["items"] if x["branch"] == "feature/x")
    assert fx["pr_number"] == STATE["pr"] and fx["active"] and fx["job_id"] == j and "etags" not in fx
    assert fx["expires_at"] > fx["pushed_at"]
    assert C.get(f"{BASE}/jobs/{j}/github").json()["watches"]


def test_poll_conditional_requests_and_external_push():
    """Unchanged resources answer 304; a commit pushed outside the app and passing checks are reported."""
    j = STATE["job"]
    full, branch = "r2a-tester/push-demo-app", "r2a/push-demo-app"
    C.post(f"{BASE}/github/poll")
    n0 = len(httpx.get(f"{GH}/_stub/requests").json())
    C.post(f"{BASE}/github/poll")
    new = httpx.get(f"{GH}/_stub/requests").json()[n0:]
    br = [r for r in new if r["path"].endswith(f"/branches/{branch}")]
    assert br and all(r["status"] == 304 for r in br), new
    work = os.path.join(TMP, "ext")
    git(TMP, "clone", "-q", "-b", branch, bare(full), work)
    open(os.path.join(work, "EXTRA.md"), "w").write("x\n")
    git(work, "add", "-A")
    git(work, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "external")
    git(work, "push", "-q", "origin", branch)
    sha = git(work, "rev-parse", "HEAD").stdout.strip()
    stubctl("ci", sha=sha, statuses=["success"], checks=[["completed", "success"], ["completed", "skipped"]])
    C.post(f"{BASE}/github/poll")
    wait_for(lambda: any(sha[:7] in n["message"] for n in notifs("github_pushed", j)))
    wait_for(lambda: any(sha[:7] in n["message"] for n in notifs("github_checks_passed", j)))
    w = next(x for x in C.get(f"{BASE}/github/watches").json()["items"] if x["branch"] == branch)
    assert w["head_sha"] == sha and w["state"]["ci"] == "success"


def test_poll_pr_merged_and_checks_fallback_to_actions():
    """check-runs 403 -> actions/runs fallback; merged via `merged`; watch finishes."""
    j, head = STATE["job"], C.get(f"{BASE}/jobs/{STATE['job']}/git").json()["head"]
    stubctl("ci", sha=head, checks_forbidden=True, runs=[["completed", "failure"]])
    stubctl("pr", full_name="r2a-tester/r2a-base", number=STATE["pr"], action="merge")
    assert C.post(f"{BASE}/github/poll").status_code == 200
    wait_for(lambda: notifs("github_pr_merged", j) and notifs("github_checks_failed", j))
    m = notifs("github_pr_merged", j)
    assert len(m) == 1 and m[0]["link"].endswith(f"/pull/{STATE['pr']}") and "merged" in m[0]["message"]
    f = notifs("github_checks_failed", j)
    assert any("r2a-base:feature/x" in x["message"] for x in f)
    w = next(x for x in C.get(f"{BASE}/github/watches").json()["items"] if x["branch"] == "feature/x")
    assert w["state"]["pr_state"] == "merged" and w["state"]["ci"] == "failure" and w["active"] is False
    assert w["state"]["checks_api"] == "unavailable"
    reqs = httpx.get(f"{GH}/_stub/requests").json()
    assert any(r["path"].endswith("/actions/runs") for r in reqs)
    C.post(f"{BASE}/github/poll")
    assert len(notifs("github_pr_merged", j)) == 1  # no repeats
    stubctl("ci", sha=head, runs=[], checks_forbidden=False)


def test_poll_pauses_when_rate_limit_low():
    assert push(STATE["job"], mode="existing", repo_full_name="r2a-tester/push-demo-app", branch="r2a/rate").status_code == 200
    httpx.post(f"{GH}/_stub/rate", params={"remaining": 10})
    C.post(f"{BASE}/github/poll")
    r = C.post(f"{BASE}/github/poll").json()
    assert r["skipped"] == "rate_low" and r["resume_in"] > 0
    assert C.get(f"{BASE}/github/watches").json()["pause_reason"] == "rate_low"
    httpx.post(f"{GH}/_stub/rate", params={"remaining": 4999})
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200  # reconnect lifts the pause
    assert "skipped" not in C.post(f"{BASE}/github/poll").json()


def test_running_and_empty_jobs():
    s = create_job(model="stub-slow")
    end = time.time() + 15
    while C.get(f"{BASE}/jobs/{s}").json()["status"] != "running" and time.time() < end:
        time.sleep(0.2)
    r = push(s, mode="new", repo_name="running-job")
    assert r.status_code == 409 and "running" in r.json()["detail"]
    C.post(f"{BASE}/jobs/{s}/stop")
    r = push(s, mode="new", repo_name="empty-job")
    assert r.status_code == 409 and "no commits" in r.json()["detail"]
    assert push("nope", mode="new").status_code == 404


def test_scope_errors_readonly_token():
    assert C.put(f"{BASE}/github/token", json={"token": READONLY}).status_code == 200
    r = push(STATE["job"], mode="new", repo_name="should-fail")
    assert r.status_code == 403 and "permissions" in r.json()["detail"] and READONLY not in r.text
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200


def test_revoked_token_on_push():
    set_stored_token(REVOKED)
    r = push(STATE["job"], mode="new", repo_name="revoked")
    assert r.status_code == 401 and "Reconnect GitHub" in r.json()["detail"] and "ghp_revoked" not in r.text
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200


def test_key_changed_token_unreadable():
    """A token encrypted with another key (R2A_SECRET_KEY changed) is reported, never used or leaked."""
    set_stored_token(TOKEN, Fernet.generate_key())
    g = C.get(f"{BASE}/github").json()
    assert g["connected"] is False and "cannot be decrypted" in g["token_error"]
    r = push(STATE["job"], mode="new", repo_name="unreadable")
    assert r.status_code == 409 and "cannot be decrypted" in r.json()["detail"]
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200
    assert C.get(f"{BASE}/github").json()["connected"]


def test_secret_key_missing_or_invalid():
    for key, word in (("", "not set"), ("not-a-fernet-key", "not a valid Fernet key")):
        with IsolatedServer(extra_env={"R2A_DATA_DIR": os.path.join(TMP, f"k{len(key)}"), "R2A_SECRET_KEY": key,
                                       **GH_ENV, "GITHUB_OAUTH_CLIENT_ID": CLIENT_ID}) as b2:
            r = httpx.put(f"{b2}/github/token", json={"token": TOKEN}, timeout=30)
            assert r.status_code == 409 and word in r.json()["detail"] and "R2A_SECRET_KEY" in r.json()["detail"], r.text
            r = httpx.post(f"{b2}/github/oauth/start", timeout=30)
            assert r.status_code == 409 and "R2A_SECRET_KEY" in r.json()["detail"]
            g = httpx.get(f"{b2}/github").json()
            assert g["connected"] is False and g["encryption"] == ("missing" if not key else "invalid")


def test_env_token_overrides_settings():
    with IsolatedServer(extra_env={"R2A_DATA_DIR": os.path.join(TMP, "envtok"), "R2A_SECRET_KEY": KEY,
                                   **GH_ENV, "R2A_GITHUB_TOKEN": TOKEN, "GITHUB_OAUTH_CLIENT_ID": CLIENT_ID}) as b2:
        g = httpx.get(f"{b2}/github", timeout=30).json()
        assert g["connected"] and g["auth_method"] == "env" and g["source"] == "env" and g["username"] == "r2a-tester"
        assert g["env_token"] is True and TOKEN not in str(g)
        for method, path, body in (("PUT", "/github/token", {"token": FINE}), ("POST", "/github/oauth/start", None),
                                   ("DELETE", "/github", None)):
            r = httpx.request(method, f"{b2}{path}", json=body, timeout=30)
            assert r.status_code == 409 and "R2A_GITHUB_TOKEN" in r.json()["detail"], (path, r.text)
        assert httpx.get(f"{b2}/github/repos?q=existing", timeout=30).json()["total"] == 1


def test_api_down_is_502():
    """GitHub API unreachable (nothing listens on the port): clear 502, nothing stored."""
    with IsolatedServer(extra_env={"R2A_DATA_DIR": os.path.join(TMP, "d2"), "R2A_SECRET_KEY": KEY, "R2A_GITHUB_TOKEN": "",
                                   "GITHUB_API_URL": f"http://127.0.0.1:{free_port()}"}) as b2:
        r = httpx.put(f"{b2}/github/token", json={"token": TOKEN}, timeout=30)
        assert r.status_code == 502 and "Could not reach GitHub" in r.json()["detail"]
        assert httpx.get(f"{b2}/github").json()["connected"] is False


def test_token_reaches_git_only_via_env():
    """The token reaches git only as a host-scoped http.<host>.extraHeader in GIT_CONFIG_* env vars:
    never argv, the remote URL or .git/config; git output is redacted."""
    import base64
    import inspect
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import git_cli
    import github_integration as gi
    assert "--force" not in inspect.getsource(gi.push_sync)
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="x"')
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    root = os.path.join(TMP, "hdr")
    git(TMP, "init", "-q", "-b", "main", root)
    git(root, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "c")
    calls, real_run = [], git_cli.run

    def spy(cwd, *args, **kw):
        calls.append((args, kw.get("env") or {}))
        return real_run(cwd, *args, **kw)

    git_cli.run = spy
    try:
        url = f"http://127.0.0.1:{srv.server_port}/o/r.git"
        try:
            gi.push_sync(root, url, "main", TOKEN, "r2a-tester", timeout=30)
            raise AssertionError("push should fail")
        except gi.GitHubError as e:
            assert e.status == 401 and TOKEN not in e.message, e.message
    finally:
        git_cli.run = real_run
        srv.shutdown()
    basic = base64.b64encode(f"r2a-tester:{TOKEN}".encode()).decode()
    assert seen and seen[0] == f"Basic {basic}"
    args, env = calls[0]
    assert TOKEN not in " ".join(args) and basic not in " ".join(args) and url in args
    assert env["GIT_CONFIG_KEY_1"] == f"http.http://127.0.0.1:{srv.server_port}/.extraHeader" and env["GIT_CONFIG_VALUE_0"] == ""
    assert TOKEN not in open(os.path.join(root, ".git", "config")).read()
    assert gi.auth_env("u", "t", "https://github.com/")["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert gi.classify_push(" ! [rejected] main -> main (non-fast-forward)") == "non_fast_forward"
    assert gi.classify_push("remote: Permission to o/r.git denied to u.") == "forbidden"
    assert gi.classify_push("refusing to allow a Personal Access Token to create or update workflow without `workflow` scope") == "workflow_permission"
    assert gi.classify_push("remote: error: GH013: Repository rule violations found") == "rules"
    assert gi.classify_push("remote: Repository not found.") == "not_found"
    assert gi.push_status("timeout", "b")[0] == 504 and gi.PUSH_TIMEOUT == 300


def test_clone_url_must_be_https_without_flag():
    import importlib
    os.environ.pop("R2A_GITHUB_ALLOW_FILE_REMOTES", None)
    os.environ["GITHUB_API_URL"] = "https://api.github.com"
    gi = importlib.import_module("github_integration")
    assert gi.check_clone_url("https://github.com/a/b.git")
    for bad in ("/tmp/x.git", "file:///tmp/x", "http://github.com/a/b", "https://evil.example/a/b.git", "ext::sh -c x"):
        try:
            gi.check_clone_url(bad)
            raise AssertionError(bad)
        except gi.GitHubError as e:
            assert e.status == 502
    assert gi.slugify("  Hello, World! v2 ", "f") == "hello-world-v2" and gi.slugify("!!!", "fb") == "fb"
    import github_client as gc
    assert gc.redact(f"x {TOKEN} y") == "x *** y" and gc.redact("custom-secret-1", "custom-secret-1") == "***"
    assert gc.redact("https://u:pw@github.com/a") == "https://***@github.com/a"
    assert gc.redact("Authorization: Bearer abc.def") == "Authorization: Bearer ***"
    assert gc.redact("github_pat_11AAAAAAAAAAAAAAAAAAAAAA_bbbb") == "***"
    assert gi.ci_state({}) == "none"
    assert gi.ci_state({"status": "pending", "status_count": 0, "checks": ["success", None]}) == "pending"
    assert gi.ci_state({"status": "success", "status_count": 1, "checks": ["success", "skipped"]}) == "success"
    assert gi.ci_state({"checks": ["success", "failure"]}) == "failure"
    assert gi.ci_state({"status": "failure", "status_count": 1}) == "failure"


def test_auto_push_on_completion():
    r = C.put(f"{BASE}/github/settings", json={"auto_push": {"enabled": True, "private": False}})
    assert r.status_code == 200 and r.json()["auto_push"] == {"enabled": True, "private": False}
    assert C.put(f"{BASE}/github/settings", json={"auto_push": {"enabled": "maybe"}}).status_code == 422
    j = create_job(roadmap="# Auto Pushed\n\n### One\nx\n\n### Two\ny\n")
    wait(j)
    end = time.time() + 20
    while time.time() < end and not C.get(f"{BASE}/jobs/{j}/github").json()["last_push"]:
        time.sleep(0.3)
    lp = C.get(f"{BASE}/jobs/{j}/github").json()["last_push"]
    assert lp and lp["source"] == "auto" and lp["repo"]["full_name"] == f"r2a-tester/auto-pushed-{j[:6]}"
    assert lp["repo"]["private"] is False and lp["branch"] == "r2a/auto-pushed"
    assert any("GitHub: auto-pushed 2 commits" in m for m in logs(j))
    # resume/restart semantics: a failing job is not auto-pushed
    e = create_job(model="stub-503")
    wait(e)
    time.sleep(1)
    assert C.get(f"{BASE}/jobs/{e}/github").json()["last_push"] is None
    # auto-push failure is logged, job stays done
    set_stored_token(REVOKED)
    f = create_job(roadmap="# Auto Fail\n\n### One\nx\n")
    job = wait(f)
    end = time.time() + 15
    while time.time() < end and not any("auto-push failed" in m for m in logs(f)):
        time.sleep(0.3)
    assert job["status"] == "done" and any("GitHub: auto-push failed - GitHub rejected the token" in m for m in logs(f))
    C.put(f"{BASE}/github/token", json={"token": TOKEN})
    C.put(f"{BASE}/github/settings", json={"auto_push": {"enabled": False}})


def test_disconnect():
    r = C.delete(f"{BASE}/github")
    g = r.json()
    assert r.status_code == 200 and g["connected"] is False and g["username"] is None
    doc = SRV.db().integrations.find_one({"_id": "github"})
    assert "token" not in doc and "token_enc" not in doc
    assert g["auto_push"]["enabled"] is False  # settings survive a disconnect
    assert push(STATE["job"], mode="new").status_code == 409


def device(code, action):
    assert httpx.post(f"{GH}/_stub/device", params={"user_code": code, "action": action}).status_code == 200


def poll_until(statuses, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = C.post(f"{BASE}/github/oauth/poll")
        assert r.status_code == 200, r.text
        if r.json()["status"] in statuses:
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"poll never reached {statuses}")


def test_oauth_unconfigured_and_client_id_setting():
    g = C.get(f"{BASE}/github").json()
    assert g["oauth"] == {"available": False, "client_id": None, "client_id_source": None, "pending": None}
    r = C.post(f"{BASE}/github/oauth/start")
    assert r.status_code == 409 and "GITHUB_OAUTH_CLIENT_ID" in r.json()["detail"]
    assert C.put(f"{BASE}/github/settings", json={"oauth_client_id": "bad id!"}).status_code == 422
    r = C.put(f"{BASE}/github/settings", json={"oauth_client_id": "Iv1.wrongclient00"})
    assert r.json()["oauth"]["available"] and r.json()["oauth"]["client_id_source"] == "settings"
    r = C.post(f"{BASE}/github/oauth/start")
    assert r.status_code == 422 and "does not know this OAuth client ID" in r.json()["detail"]
    C.put(f"{BASE}/github/settings", json={"oauth_client_id": CLIENT_ID})
    assert C.post(f"{BASE}/github/oauth/poll").status_code == 409  # nothing pending


def test_oauth_exclusive_with_pat():
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200
    assert C.get(f"{BASE}/github").json()["auth_method"] == "pat"
    r = C.post(f"{BASE}/github/oauth/start")
    assert r.status_code == 409 and "personal access token" in r.json()["detail"]
    C.delete(f"{BASE}/github")


def test_oauth_device_flow_connects():
    r = C.post(f"{BASE}/github/oauth/start")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["user_code"].startswith("ABCD-") and p["verification_uri"] == "https://github.com/login/device"
    assert p["interval"] == 1 and p["expires_at"].endswith("Z") and "device_code" not in p
    g = C.get(f"{BASE}/github").json()
    assert g["oauth"]["pending"]["user_code"] == p["user_code"] and "dc-" not in str(g)
    # a PAT can't be connected while the device flow is pending; a second start is fine (new code)
    r = C.put(f"{BASE}/github/token", json={"token": TOKEN})
    assert r.status_code == 409 and "in progress" in r.json()["detail"]
    assert poll_until(("pending",))["interval"] == 1
    device(p["user_code"], "slow_down")
    time.sleep(1.1)
    sd = poll_until(("slow_down",))
    assert sd["interval"] == 2
    device(p["user_code"], "approve")
    res = poll_until(("connected",))
    gh = res["github"]
    assert gh["connected"] and gh["auth_method"] == "oauth" and gh["username"] == "r2a-tester" and gh["oauth"]["pending"] is None
    assert gh["token_type"] == "oauth" and "repo" in gh["scopes"] and TOKEN not in str(res)
    # exclusivity the other way round
    r = C.put(f"{BASE}/github/token", json={"token": TOKEN})
    assert r.status_code == 409 and "OAuth" in r.json()["detail"]
    assert C.post(f"{BASE}/github/oauth/start").status_code == 409
    # the OAuth token works for the API
    assert C.get(f"{BASE}/github/repos?q=existing").json()["total"] == 1
    d = C.delete(f"{BASE}/github").json()
    assert d["connected"] is False and d["auth_method"] is None


def test_oauth_denied_expired_cancel():
    for action, status in (("deny", "denied"), ("expire", "expired")):
        p = C.post(f"{BASE}/github/oauth/start").json()
        device(p["user_code"], action)
        time.sleep(1.1)
        res = poll_until((status,))
        assert res["github"]["connected"] is False and res["github"]["oauth"]["pending"] is None
    p = C.post(f"{BASE}/github/oauth/start").json()
    g = C.post(f"{BASE}/github/oauth/cancel").json()
    assert g["oauth"]["pending"] is None and C.post(f"{BASE}/github/oauth/poll").status_code == 409
    # after cancel a PAT can be connected again
    assert C.put(f"{BASE}/github/token", json={"token": TOKEN}).status_code == 200
    C.delete(f"{BASE}/github")
    C.put(f"{BASE}/github/settings", json={"oauth_client_id": ""})
    assert C.get(f"{BASE}/github").json()["oauth"]["available"] is False


def test_oauth_poll_respects_interval():
    """Polling faster than the interval never reaches GitHub."""
    C.put(f"{BASE}/github/settings", json={"oauth_client_id": CLIENT_ID})
    C.post(f"{BASE}/github/oauth/start")
    n0 = len([r for r in httpx.get(f"{GH}/_stub/requests").json() if r["path"] == "/login/oauth/access_token"])
    for _ in range(5):
        assert C.post(f"{BASE}/github/oauth/poll").json()["status"] == "pending"
    n1 = len([r for r in httpx.get(f"{GH}/_stub/requests").json() if r["path"] == "/login/oauth/access_token"])
    assert n1 - n0 <= 1, (n0, n1)
    C.post(f"{BASE}/github/oauth/cancel")
    C.put(f"{BASE}/github/settings", json={"oauth_client_id": ""})


def test_delete_job_keeps_remote():
    j = STATE["job"]
    assert C.delete(f"{BASE}/jobs/{j}").status_code == 200
    assert os.path.isdir(bare("r2a-tester/push-demo-app"))  # remote untouched; only the local repo goes
    assert SRV.db().github_watches.count_documents({"job_id": j}) == 0  # watches stop with the job


def test_notification_settings_include_github_events():
    cfg = C.get(f"{BASE}/notifications/settings").json()
    for e in ("github_pushed", "github_pr_opened", "github_pr_merged", "github_pr_closed", "github_checks_passed",
              "github_checks_failed"):
        assert cfg["in_app"]["events"][e] is True and cfg["email"]["events"][e] is False


if __name__ == "__main__":
    stub = subprocess.Popen(["/app/venv/bin/python", os.path.join(os.path.dirname(os.path.abspath(__file__)), "github_stub.py"),
                             "--port", str(GH_PORT), "--root", GH_ROOT], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                httpx.get(f"{GH}/_stub/requests", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        with SRV as base:
            BASE = base
            code = run_tests(globals())
    finally:
        stub.terminate()
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
