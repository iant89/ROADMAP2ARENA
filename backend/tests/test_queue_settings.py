"""Tests for the job queue (/api/queue, queued/paused/cancelled) and runtime settings
(/api/settings) on feat/tabs-and-queue.

pytest-style; pytest is not installed, so run directly (optionally naming tests):
    /app/venv/bin/python /app/backend/tests/test_queue_settings.py [test_name ...]
State shared across tests in one process is kept in S; tests that need earlier
state are noted. Only the local stub on :9090 is used. test_zz_cleanup always runs:
it cancels/stops leftovers, resets settings (POST /api/settings/reset) and deletes
only the jobs created here (plus restarts of them) from Mongo.
"""
from __future__ import annotations

import json
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
FINISHED = ("done", "error", "stopped", "cancelled")


def roadmap(n, tag):
    return f"# test_{SUFFIX}_{tag}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def no_id(o, path="$"):
    if isinstance(o, dict):
        assert "_id" not in o, f"_id leaked at {path}"
        for k, v in o.items():
            no_id(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            no_id(v, f"{path}[{i}]")


def get(path, **kw):
    r = c.get(f"{BASE}{path}", **kw)
    if r.headers.get("content-type", "").startswith("application/json"):
        no_id(r.json())
    return r


def job(jid):
    r = get(f"/jobs/{jid}")
    assert r.status_code == 200, r.text
    return r.json()


def queue():
    r = get("/queue")
    assert r.status_code == 200
    q = r.json()
    assert set(q) == {"running", "queued", "count", "waiting"}, set(q)
    pos = [x["queue_position"] for x in q["queued"]]
    assert pos == list(range(1, len(pos) + 1)), f"positions not dense: {pos}"
    assert q["count"] == len(q["queued"])
    assert q["waiting"] == sum(1 for x in q["queued"] if x["status"] == "queued")
    for x in q["queued"]:
        assert x["paused"] == (x["status"] == "paused")
    return q


def mine(q):
    return [x["job_id"] for x in q["queued"] if x["job_id"] in CREATED]


def create(model, n, tag, **extra):
    body = {"arena_url": STUB, "model": model, "roadmap_md": roadmap(n, tag), **extra}
    r = c.post(f"{BASE}/jobs", json=body)
    assert r.status_code == 201, r.text
    d = r.json()
    CREATED.append(d["job_id"])
    assert set(d) == {"job_id", "status", "queue_position"}, d
    return d


def wait(jid, timeout=40, statuses=FINISHED):
    end = time.time() + timeout
    while time.time() < end:
        j = job(jid)
        if j["status"] in statuses:
            return j
        time.sleep(0.3)
    raise AssertionError(f"{jid} not in {statuses} after {timeout}s (status {job(jid)['status']})")


def wait_running(jid, timeout=20):
    return wait(jid, timeout, statuses=("running",))


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


def running_count():
    return len(get("/jobs", params={"status": "running", "limit": 200}).json())


def no_overlap(ids):
    js = sorted((job(i) for i in ids), key=lambda j: j["started_at"])
    for a, b in zip(js, js[1:]):
        assert a["finished_at"] <= b["started_at"], f"overlap: {a['job_id']} ended {a['finished_at']} > {b['job_id']} started {b['started_at']}"
    return [j["job_id"] for j in js]


def iso_secs(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def put_settings(body, raw=None):
    if raw is not None:
        return c.put(f"{BASE}/settings", content=raw, headers={"content-type": "application/json"})
    return c.put(f"{BASE}/settings", json=body)


def log_has(jid, pattern):
    return any(re.search(pattern, e["msg"]) for e in job(jid)["log"])


# ================================================================== preflight
def test_preflight():
    q = queue()
    assert q["running"] is None and q["count"] == 0, f"queue not empty: {q}"
    s = get("/settings").json()
    S["orig_settings"] = {k: s[k] for k in ("arena_url", "model", "step_delay_seconds", "request_timeout_seconds")}
    S["env_defaults"] = s["env_defaults"]
    print(f"    original settings: {S['orig_settings']} env_defaults: {s['env_defaults']}")
    assert set(s) == {"arena_url", "model", "step_delay_seconds", "request_timeout_seconds", "updated_at", "env_defaults"}


# ================================================================== queue core
def test_q_fifo_single_running():
    a = create("stub-slow", 1, "fifoA")
    assert a["status"] == "running" and a["queue_position"] is None
    rest = [create("gpt-4o", 1, f"fifo{t}") for t in "BCD"]
    assert [(r["status"], r["queue_position"]) for r in rest] == [("queued", 1), ("queued", 2), ("queued", 3)], rest
    q = queue()
    assert q["running"]["job_id"] == a["job_id"] and q["running"]["paused"] is False
    assert mine(q) == [r["job_id"] for r in rest]
    j = job(rest[0]["job_id"])
    assert j["status"] == "queued" and j["queue_position"] == 1 and ISO_Z.match(j["queued_at"]) and j["started_at"] is None
    assert [s["status"] for s in j["steps"]] == ["pending"]
    ids = [a["job_id"]] + [r["job_id"] for r in rest]
    maxrun = 0
    end = time.time() + 30
    while time.time() < end:
        maxrun = max(maxrun, running_count())
        if all(job(i)["status"] in FINISHED for i in ids):
            break
        time.sleep(0.25)
    assert maxrun == 1, f"{maxrun} jobs running at once"
    assert all(job(i)["status"] == "done" for i in ids)
    assert no_overlap(ids) == ids, "not FIFO"
    gap = iso_secs(job(ids[1])["started_at"]) - iso_secs(job(ids[0])["finished_at"])
    assert gap < 2.0, f"next job started {gap:.1f}s after the previous ended"
    assert log_has(ids[1], r"Waiting in the queue at position 1") and log_has(ids[1], "Started from the queue")
    S["done_job"] = ids[1]


def test_q_move_pause_delete_rules():
    a = create("stub-slow", 2, "mvA")  # ~12 s running blocker
    A = a["job_id"]
    B, C, D, E = (create("gpt-4o", 1, f"mv{t}")["job_id"] for t in "BCDE")
    mv = lambda jid, body: c.post(f"{BASE}/queue/{jid}/move", json=body)
    r = mv(E, {"position": 1})
    assert r.status_code == 200 and r.json() == {"job_id": E, "status": "queued", "queue_position": 1}, r.text
    assert mine(queue()) == [E, B, C, D]
    assert mv(E, {"direction": "down"}).json()["queue_position"] == 2 and mine(queue()) == [B, E, C, D]
    assert mv(B, {"direction": "up"}).json()["queue_position"] == 1 and mine(queue()) == [B, E, C, D]
    assert mv(D, {"direction": "down"}).json()["queue_position"] == 4
    assert mv(B, {"position": 2 ** 40}).json()["queue_position"] == 4 and mine(queue()) == [E, C, D, B]
    assert mv(B, {"position": 1}).json()["queue_position"] == 1 and mine(queue()) == [B, E, C, D]
    for body in ({}, {"position": 0}, {"position": -1}, {"direction": "left"}, {"direction": "up", "position": 1},
                 {"position": "abc"}, {"position": 1.5}, {"direction": None, "position": None}, {"direction": 3}):
        r = mv(C, body)
        assert r.status_code == 422, (body, r.status_code, r.text)
    r = c.post(f"{BASE}/queue/{C}/move")
    assert r.status_code == 422, r.status_code
    r = c.post(f"{BASE}/queue/{C}/move", content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert mine(queue()) == [B, E, C, D], "failed moves changed order"
    for jid in (str(uuid.uuid4()), "nope", "x" * 300):
        for op in ("pause", "unpause"):
            assert c.post(f"{BASE}/queue/{jid}/{op}").status_code == 404, (jid, op)
        assert mv(jid, {"position": 1}).status_code == 404
        assert c.delete(f"{BASE}/queue/{jid}").status_code == 404
    # running job: 409 for all queue ops
    assert mv(A, {"direction": "up"}).status_code == 409
    for op in ("pause", "unpause"):
        assert c.post(f"{BASE}/queue/{A}/{op}").status_code == 409
    assert c.delete(f"{BASE}/queue/{A}").status_code == 409
    # wrong methods
    assert c.get(f"{BASE}/queue/{B}/pause").status_code == 405
    assert c.put(f"{BASE}/queue/{B}/move", json={"position": 1}).status_code == 405
    assert c.post(f"{BASE}/queue").status_code == 405
    # pause -> end, idempotent; unpause on queued job is a no-op 200
    r = c.post(f"{BASE}/queue/{B}/pause")
    assert r.status_code == 200 and r.json() == {"job_id": B, "status": "paused", "queue_position": 4}, r.text
    assert mine(queue()) == [E, C, D, B]
    assert c.post(f"{BASE}/queue/{B}/pause").json() == {"job_id": B, "status": "paused", "queue_position": 4}
    r = c.post(f"{BASE}/queue/{E}/unpause")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert log_has(B, r"Paused - moved to the end of the queue \(position 4\)")
    lst = [x for x in get("/jobs", params={"status": "paused"}).json() if x["job_id"] == B]
    assert lst and lst[0]["paused"] is True
    # move paused B to the front: the worker must still skip it
    assert mv(B, {"position": 1}).json() == {"job_id": B, "status": "paused", "queue_position": 1}
    # stop / resume on queued jobs -> 409
    assert c.post(f"{BASE}/jobs/{C}/stop").status_code == 409
    assert c.post(f"{BASE}/jobs/{B}/stop").status_code == 409
    assert c.post(f"{BASE}/jobs/{C}/resume").status_code == 409
    assert c.post(f"{BASE}/jobs/{B}/resume").status_code == 409
    assert c.post(f"{BASE}/jobs/{C}/restart").status_code == 409
    # DELETE D -> cancelled, kept in history, never runs
    r = c.delete(f"{BASE}/queue/{D}")
    assert r.status_code == 200 and r.json() == {"job_id": D, "status": "cancelled", "queue_position": None}, r.text
    assert c.delete(f"{BASE}/queue/{D}").status_code == 409
    for op in ("pause", "unpause"):
        assert c.post(f"{BASE}/queue/{D}/{op}").status_code == 409
    assert mv(D, {"position": 1}).status_code == 409
    assert c.post(f"{BASE}/jobs/{D}/stop").status_code == 409
    jd = job(D)
    assert jd["status"] == "cancelled" and jd["queue_position"] is None and ISO_Z.match(jd["finished_at"])
    assert mine(queue()) == [B, E, C]
    assert any(x["job_id"] == D for x in get("/jobs", params={"status": "cancelled", "limit": 200}).json())
    # A ends -> E then C run, paused B (position 1) is skipped
    wait(A, 30)
    wait(C, 30)
    assert job(E)["status"] == "done" and job(C)["status"] == "done"
    assert no_overlap([A, E, C]) == [A, E, C]
    time.sleep(1.5)
    jb, jd = job(B), job(D)
    assert jb["status"] == "paused" and jb["started_at"] is None, jb["status"]
    assert jd["status"] == "cancelled" and jd["started_at"] is None and jd["steps"][0]["status"] == "pending"
    q = queue()
    assert q["running"] is None and mine(q) == [B]
    # unpause with nothing running -> starts at once
    r = c.post(f"{BASE}/queue/{B}/unpause")
    assert r.status_code == 200 and r.json() == {"job_id": B, "status": "running", "queue_position": None}, r.text
    assert wait(B)["status"] == "done"
    assert queue()["count"] == 0
    S["cancelled_job"] = D


def test_q_concurrent_create_and_ops():
    blocker = create("stub-slow", 1, "ccA")["job_id"]
    res = parallel(8, lambda cl, i: cl.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": "gpt-4o",
                                                                  "roadmap_md": roadmap(1, f"cc{i}")}))
    for r in res:
        if r.status_code == 201:
            CREATED.append(r.json()["job_id"])
    codes = [r.status_code for r in res]
    assert codes == [201] * 8, codes
    ids = [r.json()["job_id"] for r in res]
    assert len(set(ids)) == 8
    assert all(r.json()["status"] == "queued" for r in res), [r.json() for r in res]
    assert sorted(r.json()["queue_position"] for r in res) == list(range(1, 9))
    q = queue()
    assert sorted(mine(q)) == sorted(ids) and q["count"] == 8 and running_count() == 1
    # queue order equals returned positions
    by_pos = [r.json()["job_id"] for r in sorted(res, key=lambda r: r.json()["queue_position"])]
    assert mine(q) == by_pos
    # concurrent mixed queue ops keep positions dense and consistent
    ops = [("pause", ids[0]), ("pause", ids[1]), ("move", ids[2]), ("move", ids[3]), ("delete", ids[4]),
           ("delete", ids[5]), ("pause", ids[6]), ("move", ids[7])]

    def op(cl, i):
        kind, jid = ops[i]
        if kind == "pause":
            return cl.post(f"{BASE}/queue/{jid}/pause")
        if kind == "move":
            return cl.post(f"{BASE}/queue/{jid}/move", json={"position": 1})
        return cl.delete(f"{BASE}/queue/{jid}")
    out = parallel(len(ops), op)
    assert all(r.status_code == 200 for r in out), [(r.status_code, r.text[:80]) for r in out]
    q = queue()  # asserts dense positions
    left = mine(q)
    assert sorted(left) == sorted(set(ids) - {ids[4], ids[5]})
    st = {x["job_id"]: x["status"] for x in q["queued"]}
    assert [st[i] for i in (ids[0], ids[1], ids[6])] == ["paused"] * 3
    # blocker ends -> remaining queued run one at a time; paused never start
    maxrun = 0
    queued_ids = [i for i in left if st[i] == "queued"]
    end = time.time() + 40
    while time.time() < end:
        maxrun = max(maxrun, running_count())
        if all(job(i)["status"] in FINISHED for i in [blocker] + queued_ids):
            break
        time.sleep(0.25)
    assert maxrun == 1, maxrun
    no_overlap([blocker] + queued_ids)
    assert all(job(i)["status"] == "paused" for i in (ids[0], ids[1], ids[6]))
    for i in (ids[0], ids[1], ids[6]):
        assert c.delete(f"{BASE}/queue/{i}").status_code == 200
    assert queue()["count"] == 0


def test_q_restart_resume_enqueue():
    """Needs S['done_job'] and S['cancelled_job'] from earlier tests."""
    blocker = create("stub-slow", 1, "rrA")["job_id"]
    done_id, canc = S["done_job"], S["cancelled_job"]
    r = c.post(f"{BASE}/jobs/{canc}/resume")
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": canc, "status": "queued", "queue_position": 1, "resumed_from_step": 1}, r.json()
    assert c.post(f"{BASE}/jobs/{canc}/resume").status_code == 409
    assert c.post(f"{BASE}/jobs/{canc}/restart").status_code == 409
    rs = parallel(4, lambda cl, i: cl.post(f"{BASE}/jobs/{done_id}/restart"))
    codes = sorted(x.status_code for x in rs)
    for x in rs:
        if x.status_code == 201:
            CREATED.append(x.json()["job_id"])
    assert codes == [201, 409, 409, 409], codes
    new = next(x for x in rs if x.status_code == 201).json()
    assert new["status"] == "queued" and new["queue_position"] == 2, new
    assert c.post(f"{BASE}/jobs/{done_id}/restart").status_code == 409
    assert job(new["job_id"])["restarted_from"] == done_id
    assert mine(queue()) == [canc, new["job_id"]]
    assert running_count() == 1
    for jid in (blocker, canc, new["job_id"]):
        assert wait(jid, 30)["status"] == "done", jid
    no_overlap([blocker, canc, new["job_id"]])
    assert turn_of_step(canc, 1) == 1
    assert log_has(canc, "Resumed from step 1")
    # restart allowed again once the first restart finished
    r = c.post(f"{BASE}/jobs/{done_id}/restart")
    assert r.status_code == 201, r.text
    CREATED.append(r.json()["job_id"])
    assert r.json()["status"] == "running"
    wait(r.json()["job_id"])


def turn_of_step(jid, i):
    m = TURN.search(get(f"/jobs/{jid}/steps/{i}").json()["response"] or "")
    return int(m.group(1)) if m else None


def test_q_list_filters_and_shapes():
    r = get("/jobs", params={"status": "done,cancelled", "limit": 200})
    assert r.status_code == 200 and all(x["status"] in ("done", "cancelled") for x in r.json())
    keys = {"job_id", "status", "created_at", "step_total", "steps_done", "title", "model", "failed_step",
            "stopped_step", "restarted_from", "queue_position", "queued_at", "started_at", "finished_at", "paused",
            "cloned_from"}  # added with Clone job (feat/history-panel)
    for x in r.json():
        assert set(x) == keys, set(x) ^ keys
    assert len(get("/jobs", params={"limit": 1}).json()) == 1
    assert get("/jobs", params={"limit": 200}).status_code == 200
    assert len(get("/jobs").json()) <= 20
    for params in ({"limit": 0}, {"limit": 201}, {"limit": -1}, {"limit": "abc"}, {"status": "bogus"},
                   {"status": ""}, {"status": ",,"}, {"status": "done,bogus"}, {"limit": 2 ** 70}):
        r = get("/jobs", params=params)
        assert r.status_code == 422, (params, r.status_code, r.text[:100])
    assert get("/jobs", params={"status": " done , error "}).status_code == 200
    j = job(S["done_job"])
    for k in ("queued_at", "started_at", "finished_at", "created_at"):
        assert ISO_Z.match(j[k]), (k, j[k])


# ================================================================== settings
def test_s_validation():
    before = get("/settings").json()
    bad_json = [{"arena_url": "ftp://x"}, {"arena_url": ""}, {"arena_url": "http://"}, {"arena_url": 5},
                {"arena_url": None}, {"model": ""}, {"model": "   "}, {"model": None}, {"model": 5},
                {"step_delay_seconds": -1}, {"step_delay_seconds": 600.001}, {"step_delay_seconds": "5"},
                {"step_delay_seconds": True}, {"step_delay_seconds": None}, {"step_delay_seconds": [1]},
                {"request_timeout_seconds": 9.999}, {"request_timeout_seconds": 3601}, {"request_timeout_seconds": False},
                {"request_timeout_seconds": 1e400 if False else 10 ** 30},
                {"bogus": 1}, {"model": "gpt-4o", "extra": 1}, {"_id": "x"}, {"updated_at": "2020-01-01T00:00:00Z"},
                {"env_defaults": {}}, [1], [], "str", 5, None]
    for body in bad_json:
        r = c.put(f"{BASE}/settings", content=json.dumps(body), headers={"content-type": "application/json"})
        assert r.status_code == 422, (body, r.status_code, r.text[:120])
        assert "detail" in r.json()
    for raw in (b"{bad", b"", b'{"step_delay_seconds": NaN}', b'{"step_delay_seconds": Infinity}',
                b'{"request_timeout_seconds": -Infinity}', b'{"step_delay_seconds": 1e400}'):
        r = put_settings(None, raw=raw)
        assert r.status_code == 422, (raw, r.status_code, r.text[:120])
    assert c.post(f"{BASE}/settings").status_code == 405
    assert c.delete(f"{BASE}/settings").status_code == 405
    assert c.get(f"{BASE}/settings/reset").status_code == 405
    after = get("/settings").json()
    assert after == before, "rejected PUTs changed settings"


def test_s_persist_boundaries_and_reset():
    for body, exp in (({"step_delay_seconds": 0, "request_timeout_seconds": 10}, (0, 10)),
                      ({"step_delay_seconds": 600, "request_timeout_seconds": 3600}, (600, 3600)),
                      ({"step_delay_seconds": 0.5, "request_timeout_seconds": 10.5}, (0.5, 10.5))):
        r = put_settings(body)
        assert r.status_code == 200, r.text
        assert (r.json()["step_delay_seconds"], r.json()["request_timeout_seconds"]) == exp
    r = put_settings({"model": f"  test_{SUFFIX}_model ✨  ", "arena_url": " https://example.invalid/x "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["model"] == f"test_{SUFFIX}_model ✨" and d["arena_url"] == "https://example.invalid/x"
    assert d["step_delay_seconds"] == 0.5  # partial PUT keeps other fields
    assert ISO_Z.match(d["updated_at"]) and d["env_defaults"] == S["env_defaults"]
    g = get("/settings").json()
    assert g == d
    cfg = get("/config").json()
    assert cfg == {k: d[k] for k in ("arena_url", "model", "step_delay_seconds", "request_timeout_seconds")}
    assert put_settings({}).status_code == 200  # empty partial update is a no-op
    r = c.post(f"{BASE}/settings/reset")
    assert r.status_code == 200
    assert {k: r.json()[k] for k in S["env_defaults"]} == S["env_defaults"]
    assert {k: get("/settings").json()[k] for k in S["env_defaults"]} == S["env_defaults"]
    assert c.post(f"{BASE}/settings/reset").status_code == 200  # repeat


def test_s_new_runs_use_settings():
    # model + delay + timeout from settings apply to a new job created without arena_url/model
    assert put_settings({"model": "stub-503", "arena_url": STUB, "step_delay_seconds": 0,
                         "request_timeout_seconds": 15}).status_code == 200
    r = c.post(f"{BASE}/jobs", json={"roadmap_md": roadmap(2, "setA")})
    assert r.status_code == 201
    a = r.json()["job_id"]
    CREATED.append(a)
    j = wait(a)
    assert j["model"] == "stub-503" and j["arena_url"] == STUB and j["status"] == "error"
    assert "check that the arena2api Chrome tab is open" in j["error"]
    assert log_has(a, r"Job started: .*step delay 0s, timeout 15s"), [e["msg"] for e in j["log"]]
    # delay 0 -> steps back to back
    assert put_settings({"model": "gpt-4o"}).status_code == 200
    b = create("gpt-4o", 3, "setB")["job_id"]
    wait(b)
    gaps = [iso_secs(step(b, i + 1)["started_at"]) - iso_secs(step(b, i)["finished_at"]) for i in (1, 2)]
    assert all(g < 1.0 for g in gaps), gaps
    # delay 3 -> gap of ~3 s; a settings change during the run does not affect it
    assert put_settings({"step_delay_seconds": 3}).status_code == 200
    d = create("gpt-4o", 2, "setD")["job_id"]
    time.sleep(0.3)
    assert put_settings({"step_delay_seconds": 0, "request_timeout_seconds": 20}).status_code == 200
    wait(d)
    gap = iso_secs(step(d, 2)["started_at"]) - iso_secs(step(d, 1)["finished_at"])
    assert 2.8 <= gap < 4.5, gap
    assert log_has(d, r"step delay 3s, timeout 15s")
    # resume uses the current settings (delay 0, timeout 20) and the override model
    r = c.post(f"{BASE}/jobs/{a}/resume", json={"model": "gpt-4o"})
    assert r.status_code == 200 and r.json()["status"] == "running", r.text
    j = wait(a)
    assert j["status"] == "done" and j["model"] == "gpt-4o"
    assert log_has(a, r"Rebuilt history|Job started") and log_has(a, r"step delay 0s, timeout 20s")
    # restart uses current settings too
    r = c.post(f"{BASE}/jobs/{d}/restart")
    assert r.status_code == 201
    CREATED.append(r.json()["job_id"])
    wait(r.json()["job_id"])
    assert log_has(r.json()["job_id"], r"step delay 0s, timeout 20s")


def step(jid, i):
    r = get(f"/jobs/{jid}/steps/{i}")
    assert r.status_code == 200
    return r.json()


# ================================================================== cleanup
def test_zz_cleanup():
    for jid in CREATED:
        st = c.get(f"{BASE}/jobs/{jid}").json().get("status")
        if st in ("queued", "paused"):
            c.delete(f"{BASE}/queue/{jid}")
    for jid in CREATED:
        if c.get(f"{BASE}/jobs/{jid}").json().get("status") == "running":
            c.post(f"{BASE}/jobs/{jid}/stop")
    for jid in CREATED:
        wait(jid, 30)
    r = c.post(f"{BASE}/settings/reset")
    assert r.status_code == 200
    from pymongo import MongoClient
    env = dict(l.strip().split("=", 1) for l in open("/app/backend/.env") if "=" in l and not l.startswith("#"))
    db = MongoClient(env["MONGO_URL"], serverSelectionTimeoutMS=5000)[env["DB_NAME"]]
    ids = set(CREATED) | {j["id"] for j in db.jobs.find({"restarted_from": {"$in": CREATED}}, {"_id": 0, "id": 1})}
    db.steps.delete_many({"job_id": {"$in": list(ids)}})
    db.jobs.delete_many({"id": {"$in": list(ids)}})
    q = queue()
    assert q["running"] is None and q["count"] == 0, q
    s = get("/settings").json()
    assert {k: s[k] for k in s["env_defaults"]} == s["env_defaults"]


if __name__ == "__main__":
    names = [n for n in list(globals()) if n.startswith("test_")]
    only = sys.argv[1:]
    passed, failed = 0, []
    for n in names:
        if only and n not in only and n not in ("test_preflight", "test_zz_cleanup"):
            continue
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
