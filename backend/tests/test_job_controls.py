"""Tests for POST /api/jobs/{id}/stop, /restart, /resume (feat/job-controls).

pytest-style; pytest is not installed, so run directly:
    /app/venv/bin/python /app/backend/tests/test_job_controls.py
Functions run in file order and share state. Uses only the local stub on :9090.
Created jobs are deleted from Mongo at the end (only ids created here).
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import threading
import time
import traceback
import uuid

import httpx

BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8001").rstrip("/") + "/api"
STUB = "http://127.0.0.1:9090"
SUFFIX = secrets.token_hex(3)
CREATED: list[str] = []
S: dict = {}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
c = httpx.Client(timeout=30)
TURN = re.compile(r"\(stub reply for user turn (\d+) of the conversation\)")


def roadmap(n, tag):
    return f"# test_{SUFFIX}_{tag}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def no_id(o):
    if isinstance(o, dict):
        assert "_id" not in o
        [no_id(v) for v in o.values()]
    elif isinstance(o, list):
        [no_id(v) for v in o]


def job(jid):
    r = c.get(f"{BASE}/jobs/{jid}")
    assert r.status_code == 200, r.text
    no_id(r.json())
    return r.json()


def step(jid, i):
    r = c.get(f"{BASE}/jobs/{jid}/steps/{i}")
    assert r.status_code == 200
    return r.json()


def turn_of(resp):
    m = TURN.search(resp or "")
    return int(m.group(1)) if m else None


def create(model, n, tag, ctx=""):
    r = c.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": model, "roadmap_md": roadmap(n, tag),
                                     "project_context": ctx})
    assert r.status_code == 201, r.text
    CREATED.append(r.json()["job_id"])
    return r.json()["job_id"]


def wait(jid, timeout=40, statuses=("done", "error", "stopped")):
    end = time.time() + timeout
    while time.time() < end:
        j = job(jid)
        if j["status"] in statuses:
            return j
        time.sleep(0.3)
    raise AssertionError(f"{jid} not finished in {timeout}s")


def wait_step_running(jid, idx, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        j = job(jid)
        if j["steps"][idx - 1]["status"] == "running":
            return j
        assert j["status"] == "running", j["status"]
        time.sleep(0.2)
    raise AssertionError("step never running")


def parallel(n, fn):
    out, bar = [None] * n, threading.Barrier(n)

    def go(i):
        cl = httpx.Client(timeout=30)
        bar.wait()
        out[i] = fn(cl, i)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def no_job_running():
    return not any(j["status"] == "running" for j in c.get(f"{BASE}/jobs").json())


# ------------------------------------------------------------------ no-job checks
def test_preflight_no_job_running():
    assert no_job_running(), "a job is already running - cannot test"


def test_unknown_malformed_ids_and_405():
    for jid in (str(uuid.uuid4()), "not-a-uuid", "x" * 300):
        for action in ("stop", "restart", "resume"):
            r = c.post(f"{BASE}/jobs/{jid}/{action}")
            assert r.status_code == 404, (jid, action, r.status_code)
            assert "detail" in r.json()
    for action in ("stop", "restart", "resume"):
        jid = str(uuid.uuid4())
        assert c.get(f"{BASE}/jobs/{jid}/{action}").status_code == 405
        assert c.put(f"{BASE}/jobs/{jid}/{action}").status_code == 405
        assert c.delete(f"{BASE}/jobs/{jid}/{action}").status_code == 405


# ------------------------------------------------------------------ stop mid-step
def test_stop_mid_step():
    jid = create("stub-slow-at-2", 3, "stop", ctx=f"test_{SUFFIX} ctx ✨")
    S["stop"] = jid
    wait_step_running(jid, 2)
    S["s1_before"] = step(jid, 1)
    r = c.post(f"{BASE}/jobs/{jid}/stop")
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": jid, "status": "stopped", "stopped_step": 2, "steps_done": 1}, r.json()
    j = job(jid)
    assert j["status"] == "stopped" and j["stopped_step"] == 2 and j["steps_done"] == 1
    assert j["error"] is None and j["failed_step"] is None and j["restarted_from"] is None
    assert ISO_Z.match(j["finished_at"])
    assert [s["status"] for s in j["steps"]] == ["done", "stopped", "pending"]
    assert any(e["level"] == "warn" and "stopped at step 2" in e["msg"] for e in j["log"])
    lst = [x for x in c.get(f"{BASE}/jobs").json() if x["job_id"] == jid][0]
    assert lst["status"] == "stopped" and lst["stopped_step"] == 2


def test_stop_repeat_and_new_job_allowed():
    jid = S["stop"]
    r = c.post(f"{BASE}/jobs/{jid}/stop")
    assert r.status_code == 409, (r.status_code, r.text)
    assert no_job_running()


def test_abandoned_request_never_flips_step():
    jid = S["stop"]
    time.sleep(6)  # stub-slow-at-2 sleeps 5 s
    j = job(jid)
    assert j["status"] == "stopped" and j["steps_done"] == 1
    assert [s["status"] for s in j["steps"]] == ["done", "stopped", "pending"]
    s1 = step(jid, 1)
    assert s1 == S["s1_before"], "step 1 changed after stop"
    assert turn_of(s1["response"]) == 1 and s1["artifacts"]
    s2 = step(jid, 2)
    assert s2["status"] == "stopped" and s2["response"] == "" and s2["artifacts"] == []
    # download still works with step 1 artifacts
    assert c.get(f"{BASE}/jobs/{jid}/download").status_code == 200


def test_resume_validation_422():
    jid = S["stop"]
    for body in ({"arena_url": "ftp://x"}, {"arena_url": ""}, {"model": ""}, {"model": "   "},
                 {"model": 5}, {"arena_url": ["x"]}):
        r = c.post(f"{BASE}/jobs/{jid}/resume", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)
    r = c.post(f"{BASE}/jobs/{jid}/resume", content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422, r.status_code
    for body in ({"arena_url": "ftp://x"}, {"model": ""}, {"model": None, "arena_url": 3}):
        r = c.post(f"{BASE}/jobs/{jid}/restart", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)
    assert job(jid)["status"] == "stopped"
    assert no_job_running()


def test_resume_stopped_job_rebuilds_history():
    jid = S["stop"]
    r = c.post(f"{BASE}/jobs/{jid}/resume")
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": jid, "status": "running", "resumed_from_step": 2}
    j = job(jid)
    assert j["status"] == "running" and j["stopped_step"] is None and j["finished_at"] is None
    # while running: second resume, new job, restart of another, stop of nothing else -> 409
    assert c.post(f"{BASE}/jobs/{jid}/resume").status_code == 409
    r = c.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "blocked")})
    if r.status_code == 201:
        CREATED.append(r.json()["job_id"])
    assert r.status_code == 409
    assert c.post(f"{BASE}/jobs/{jid}/restart").status_code == 409
    j = wait(jid)
    assert j["status"] == "done", (j["status"], j["error"])
    assert j["steps_done"] == 3 and j["error"] is None and j["stopped_step"] is None
    assert any("Resumed from step 2" in e["msg"] for e in j["log"])
    assert step(jid, 1) == S["s1_before"], "step 1 changed by resume"
    s2, s3 = step(jid, 2), step(jid, 3)
    assert turn_of(s2["response"]) == 2, s2["response"][:80]
    assert turn_of(s3["response"]) == 3, s3["response"][:80]
    assert "app/main.py" in s2["prompt"] and "pyproject.toml" in s2["prompt"]
    assert "PREVIOUSLY CREATED FILES" in s2["prompt"]


def test_done_job_rejects_stop_and_resume():
    jid = S["stop"]
    for action in ("stop", "resume"):
        r = c.post(f"{BASE}/jobs/{jid}/{action}")
        assert r.status_code == 409, (action, r.status_code, r.text)
        r = c.post(f"{BASE}/jobs/{jid}/{action}")
        assert r.status_code == 409


def test_concurrent_restart_one_wins():
    old = S["stop"]
    res = parallel(6, lambda cl, i: cl.post(f"{BASE}/jobs/{old}/restart", json={"model": "gpt-4o"}))
    ok = [r for r in res if r.status_code == 201]
    for r in ok:
        CREATED.append(r.json()["job_id"])
    codes = sorted(r.status_code for r in res)
    running = [x["job_id"] for x in c.get(f"{BASE}/jobs").json() if x["status"] == "running"]
    for r in ok:
        wait(r.json()["job_id"])
    assert len(ok) == 1 and codes.count(409) == 5, codes
    new = ok[0].json()["job_id"]
    assert set(ok[0].json()) == {"job_id"} and new != old and uuid.UUID(new).version == 4
    assert running == [new], running
    S["restarted"] = new


def test_restarted_job_contents():
    old, new = S["stop"], S["restarted"]
    o, n = job(old), job(new)
    assert n["restarted_from"] == old and n["status"] == "done", (n["restarted_from"], n["status"])
    assert n["model"] == "gpt-4o" and n["arena_url"] == o["arena_url"]
    assert n["project_context"] == o["project_context"] and n["roadmap_md"] == o["roadmap_md"]
    assert n["title"] == o["title"] and n["step_total"] == 3 and n["steps_done"] == 3
    assert turn_of(step(new, 1)["response"]) == 1
    assert o["status"] == "done" and o["restarted_from"] is None  # old job untouched
    assert any(e["msg"] == f"Restart of job {old}" for e in n["log"])


# ------------------------------------------------------------------ error + resume with override
def test_error_job_concurrent_resume_with_override():
    jid = create("stub-503-at-3", 3, "err")
    S["err"] = jid
    j = wait(jid)
    assert j["status"] == "error" and j["failed_step"] == 3
    before = [step(jid, i) for i in (1, 2)]
    # mixed concurrent: 4 resumes + 1 new job + 1 restart of another job
    def fn(cl, i):
        if i < 4:
            return cl.post(f"{BASE}/jobs/{jid}/resume", json={"model": "gpt-4o"})
        if i == 4:
            return cl.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "race")})
        return cl.post(f"{BASE}/jobs/{S['restarted']}/restart")
    res = parallel(6, fn)
    for r in res:
        if r.status_code == 201:
            CREATED.append(r.json()["job_id"])
    codes = [r.status_code for r in res]
    running = [x["job_id"] for x in c.get(f"{BASE}/jobs").json() if x["status"] == "running"]
    for x in list(CREATED):
        wait(x)
    winners = [r for r in res if r.status_code in (200, 201)]
    assert len(winners) == 1 and codes.count(409) == 5, codes
    assert len(running) == 1, running
    if res.index(winners[0]) >= 4:  # resume lost the race; resume now
        r = c.post(f"{BASE}/jobs/{jid}/resume", json={"model": "gpt-4o"})
        assert r.status_code == 200
    else:
        r = winners[0]
    assert r.json()["resumed_from_step"] == 3
    j = wait(jid)
    assert j["status"] == "done" and j["model"] == "gpt-4o" and j["error"] is None and j["failed_step"] is None
    assert any("Resumed from step 3" in e["msg"] and "stub-503-at-3 -> gpt-4o" in e["msg"] for e in j["log"])
    assert [step(jid, i) for i in (1, 2)] == before
    assert turn_of(step(jid, 3)["response"]) == 3


def test_resume_with_503_once_model():
    n = int(os.environ.get("ONCE_N", "4"))  # fresh N per stand-in process (2 and 3 already used)
    model = f"stub-503-once-at-{n}"
    jid = create(model, n + 1, "once")
    j = wait(jid)
    if j["status"] == "done":
        raise AssertionError(f"{model} did not fire (already used in this stub process)")
    assert j["status"] == "error" and j["failed_step"] == n
    assert c.post(f"{BASE}/jobs/{jid}/resume").json()["resumed_from_step"] == n
    j = wait(jid)
    assert j["status"] == "done" and j["model"] == model
    assert [turn_of(step(jid, i)["response"]) for i in range(1, n + 2)] == list(range(1, n + 2))


# ------------------------------------------------------------------ concurrent stops, stop between steps
def _concurrent_stops_once(tag, n_calls=5):
    jid = create("stub-slow-at-2", 3, tag)
    wait_step_running(jid, 2)
    res = parallel(n_calls, lambda cl, i: cl.post(f"{BASE}/jobs/{jid}/stop"))
    codes = sorted(r.status_code for r in res)
    assert all(code in (200, 409) for code in codes) and 200 in codes, codes
    for r in res:
        if r.status_code == 200:
            assert r.json() == {"job_id": jid, "status": "stopped", "stopped_step": 2, "steps_done": 1}, r.json()
    j = job(jid)
    assert j["status"] == "stopped", (j["status"], codes)
    assert j["stopped_step"] == 2, f"stopped_step={j['stopped_step']} codes={codes}"
    assert [s["status"] for s in j["steps"]] == ["done", "stopped", "pending"]
    stops = [e for e in j["log"] if e["msg"].startswith("Job stopped")]
    assert len(stops) == 1, [e["msg"] for e in stops]
    assert no_job_running()
    return jid


def test_concurrent_stops_mid_step():
    for i in range(3):
        jid = _concurrent_stops_once(f"cstop{i}", n_calls=4 + i)
        # a new job can start right afterwards
        nid = create("gpt-4o", 1, f"after{i}")
        wait(nid)
    S["cstop"] = jid
    time.sleep(5.5)  # abandoned slow request must not flip step 2
    j = job(jid)
    assert j["status"] == "stopped" and [s["status"] for s in j["steps"]] == ["done", "stopped", "pending"]


def test_stop_racing_natural_finish():
    """Stop sent around the moment a 1-step stub-slow-at-1 job finishes: 200+stopped or 409+done, never 500."""
    seen = []
    for offset in (4.85, 4.95, 5.0, 5.05, 5.15):
        jid = create("stub-slow-at-1", 1, f"race{int(offset * 100)}")
        wait_step_running(jid, 1)
        time.sleep(offset - 0.1)
        res = parallel(3, lambda cl, i: cl.post(f"{BASE}/jobs/{jid}/stop"))
        codes = sorted(r.status_code for r in res)
        j = wait(jid)
        seen.append((offset, codes, j["status"], j["stopped_step"]))
        assert all(code in (200, 409) for code in codes), seen
        if 200 in codes:
            assert j["status"] == "stopped", seen
        else:
            assert j["status"] == "done", seen
            assert all("finished as done" in r.json()["detail"] or "is done" in r.json()["detail"] for r in res), \
                [r.json() for r in res]
        stops = [e for e in j["log"] if e["msg"].startswith("Job stopped")]
        assert len(stops) == (1 if j["status"] == "stopped" else 0), (seen, [e["msg"] for e in stops])
        assert no_job_running()
    print("    race outcomes:", seen)
    # stop on a done job is a plain 409
    r = c.post(f"{BASE}/jobs/{CREATED[-1]}/stop")
    assert r.status_code == 409


def test_restart_from_stopped_job():
    old = S["cstop"]
    r = c.post(f"{BASE}/jobs/{old}/restart", json={})
    assert r.status_code == 201, r.text
    new = r.json()["job_id"]
    CREATED.append(new)
    assert job(new)["restarted_from"] == old
    # stop the restarted job right away (step 1 or between steps)
    time.sleep(0.5)
    r = c.post(f"{BASE}/jobs/{new}/stop")
    assert r.status_code in (200, 409), r.status_code
    wait(new)
    assert job(old)["status"] == "stopped"  # restart does not change the source job


def test_stop_between_steps_then_resume():
    jid = create("gpt-4o", 3, "between")
    end = time.time() + 10
    while time.time() < end:
        j = job(jid)
        st = [s["status"] for s in j["steps"]]
        if st[0] == "done" and st[1] == "pending":
            break
        time.sleep(0.1)
    r = c.post(f"{BASE}/jobs/{jid}/stop")
    assert r.status_code == 200, r.text
    j = job(jid)
    assert j["status"] == "stopped" and j["steps_done"] == 1
    assert [s["status"] for s in j["steps"]] == ["done", "pending", "pending"], [s["status"] for s in j["steps"]]
    assert j["stopped_step"] is None
    r = c.post(f"{BASE}/jobs/{jid}/resume")
    assert r.status_code == 200 and r.json()["resumed_from_step"] == 2
    j = wait(jid)
    assert j["status"] == "done" and [turn_of(step(jid, i)["response"]) for i in (1, 2, 3)] == [1, 2, 3]


# ------------------------------------------------------------------ cleanup
def test_zz_cleanup():
    for jid in CREATED:
        if job(jid)["status"] == "running":
            c.post(f"{BASE}/jobs/{jid}/stop")
        wait(jid)
    from pymongo import MongoClient
    env = dict(l.strip().split("=", 1) for l in open("/app/backend/.env") if "=" in l and not l.startswith("#"))
    db = MongoClient(env["MONGO_URL"], serverSelectionTimeoutMS=5000)[env["DB_NAME"]]
    db.steps.delete_many({"job_id": {"$in": CREATED}})
    db.jobs.delete_many({"id": {"$in": CREATED}})
    for jid in CREATED:
        assert c.get(f"{BASE}/jobs/{jid}").status_code == 404


if __name__ == "__main__":
    names = [n for n in list(globals()) if n.startswith("test_")]
    only = sys.argv[1:]
    passed, failed = 0, []
    for n in names:
        if only and n not in only and n != "test_zz_cleanup":
            continue
        t = time.time()
        try:
            globals()[n]()
            passed += 1
            print(f"PASS {n} ({time.time() - t:.1f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            print(f"FAIL {n}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=1)
    print(f"\n{passed} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)
