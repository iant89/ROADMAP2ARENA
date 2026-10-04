"""Tests for the Job history endpoints (transcript, HTML export, files, clone).

Run: /app/venv/bin/python /app/backend/tests/test_history.py
Uses only the local arena stand-in (127.0.0.1:9090); deletes the jobs it creates.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
import traceback

import httpx

BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8001").rstrip("/") + "/api"
STUB = os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090")
TAG = f"test_hist_{secrets.token_hex(3)}"
CREATED: list[str] = []
S: dict = {}
c = httpx.Client(timeout=30)
XSS = '<script>alert("x")</script> & "quotes"'


def create(body):
    r = c.post(f"{BASE}/jobs", json=body)
    if r.status_code == 201:
        CREATED.append(r.json()["job_id"])
    return r


def wait(jid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"{BASE}/jobs/{jid}").json()
        if j["status"] in ("done", "error", "stopped", "cancelled"):
            return j
        time.sleep(0.4)
    raise AssertionError(f"{jid} did not finish")


def roadmap(n, title=TAG):
    return f"# {title}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def test_setup_done_job():
    r = create({"arena_url": STUB, "model": "gpt-4o", "project_context": XSS, "roadmap_md": roadmap(3, f"{TAG} {XSS}")})
    assert r.status_code == 201, r.text
    S["job"] = r.json()["job_id"]
    assert wait(S["job"])["status"] == "done"


def test_transcript_json():
    t = c.get(f"{BASE}/jobs/{S['job']}/transcript").json()
    assert set(t) == {"job_id", "title", "status", "model", "arena_url", "project_context", "created_at", "finished_at",
                      "step_total", "steps_done", "turns"}
    assert [x["step_index"] for x in t["turns"]] == [1, 2, 3]
    for x in t["turns"]:
        assert set(x) == {"step_index", "step_title", "status", "prompt", "response", "error", "artifact_paths",
                          "started_at", "finished_at"}
        assert x["status"] == "done" and x["prompt"] and x["response"].startswith("(stub reply for user turn")
    assert XSS in t["project_context"]
    assert c.get(f"{BASE}/jobs/nope/transcript").status_code == 404


def test_transcript_pending_steps_omitted():
    r = create({"arena_url": STUB, "model": "stub-503-at-2", "roadmap_md": roadmap(3)})
    jid = r.json()["job_id"]
    assert wait(jid)["status"] == "error"
    t = c.get(f"{BASE}/jobs/{jid}/transcript").json()
    assert [(x["step_index"], x["status"]) for x in t["turns"]] == [(1, "done"), (2, "error")]
    assert t["turns"][1]["error"] and t["turns"][1]["response"] == ""


def test_transcript_html_export():
    r = c.get(f"{BASE}/jobs/{S['job']}/transcript.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == f'attachment; filename="roadmap2arena-{S["job"][:8]}-transcript.html"'
    h = r.text
    assert h.startswith("<!DOCTYPE html>") and "<style>" in h and '<meta charset="utf-8">' in h
    assert "<script" not in h and "<link" not in h and " src=" not in h  # self-contained, user text escaped
    assert "&lt;script&gt;" in h and h.count('class="turn user"') == 3 and h.count('class="turn assistant"') == 3
    assert "<pre><code>" in h  # fenced blocks rendered as code
    assert c.get(f"{BASE}/jobs/nope/transcript.html").status_code == 404


def test_files_list_and_download():
    files = c.get(f"{BASE}/jobs/{S['job']}/files").json()
    paths = [f["path"] for f in files]
    assert paths == sorted(paths) and "app/main.py" in paths
    for f in files:
        assert set(f) == {"path", "step_index", "versions", "size", "zip_path", "zip_skip_reason"}
        assert f["versions"][-1] == f["step_index"]
    evil = next((f for f in files if ".." in f["path"]), None)
    if evil:
        assert evil["zip_path"] is None and evil["zip_skip_reason"]
    main = next(f for f in files if f["path"] == "app/main.py")
    r = c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "app/main.py"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'filename="main.py"' in r.headers["content-disposition"] and len(r.content) == main["size"]
    r1 = c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "app/main.py", "step": main["versions"][0]})
    assert r1.status_code == 200
    assert c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "nope.py"}).status_code == 404
    missing_step = next(i for i in range(1, 4) if i not in main["versions"]) if len(main["versions"]) < 3 else None
    if missing_step:
        assert c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "app/main.py", "step": missing_step}).status_code == 404
    assert c.get(f"{BASE}/jobs/{S['job']}/files/download").status_code == 422
    assert c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "x", "step": 0}).status_code == 422
    assert c.get(f"{BASE}/jobs/{S['job']}/files/download", params={"path": "x", "step": 10**20}).status_code == 422
    assert c.get(f"{BASE}/jobs/nope/files").status_code == 404


def test_clone_source_and_clone():
    src = c.get(f"{BASE}/jobs/{S['job']}/clone-source").json()
    assert set(src) == {"source_job_id", "title", "arena_url", "model", "project_context", "roadmap_md", "step_total"}
    assert src["source_job_id"] == S["job"] and src["project_context"] == XSS and src["step_total"] == 3
    assert c.get(f"{BASE}/jobs/nope/clone-source").status_code == 404
    r = create({**{k: src[k] for k in ("arena_url", "project_context")}, "model": "gpt-4o",
                "roadmap_md": src["roadmap_md"] + "\n### Step 4\nExtra.\n", "cloned_from": S["job"]})
    assert r.status_code == 201 and set(r.json()) == {"job_id", "status", "queue_position"}
    new = r.json()["job_id"]
    j = wait(new)
    assert j["cloned_from"] == S["job"] and j["step_total"] == 4 and j["status"] == "done"
    assert any(e["msg"] == f"Clone of job {S['job']}" for e in j["log"])
    assert any(x["job_id"] == new and x["cloned_from"] == S["job"] for x in c.get(f"{BASE}/jobs").json())
    bad = c.post(f"{BASE}/jobs", json={"roadmap_md": roadmap(1), "cloned_from": "does-not-exist"})
    assert bad.status_code == 422 and "cloned_from" in bad.json()["detail"]


def _api_delete(ids):
    """Delete test jobs through the API of the backend under test (BASE), never a hardcoded
    /app/backend/.env database - that is the live app DB when another checkout is tested."""
    ids = sorted(ids)
    for i in range(0, len(ids), 100):
        r = c.post(f"{BASE}/jobs/bulk-delete", json={"job_ids": ids[i:i + 100]})
        assert r.status_code == 200, r.text


def test_zz_cleanup():
    for jid in CREATED:
        if c.get(f"{BASE}/jobs/{jid}").json().get("status") in ("queued", "paused", "running"):
            wait(jid)
    _api_delete(set(CREATED))


if __name__ == "__main__":
    names = [n for n in list(globals()) if n.startswith("test_")]
    passed, failed = 0, []
    for n in names:
        t = time.time()
        try:
            globals()[n]()
            passed += 1
            print(f"PASS {n} ({time.time() - t:.1f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            print(f"FAIL {n}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=2)
    print(f"\n{passed} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)
