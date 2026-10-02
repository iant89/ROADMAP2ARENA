"""Tests for hard job deletion and the renamed queue cancel (PR feat/job-deletion).

Run: /app/venv/bin/python /app/backend/tests/test_deletion.py
Starts its OWN backend on a free port against a throwaway database (see isolated_server.py),
because "delete all finished" would otherwise wipe the dev history. Needs the arena stand-in
on 127.0.0.1:9090 (models: gpt-4o instant, stub-slow 5 s per call, stub-503 fails).
"""
from __future__ import annotations

import concurrent.futures as cf
import sys
import time

import httpx

from isolated_server import IsolatedServer, run_tests

STUB = "http://127.0.0.1:9090"
B = ""  # set in main
c = httpx.Client(timeout=30)
SRV: IsolatedServer | None = None


def roadmap(n, title="del test"):
    return f"# {title}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def create(model="gpt-4o", n=2, title="del test"):
    r = c.post(f"{B}/jobs", json={"arena_url": STUB, "model": model, "roadmap_md": roadmap(n, title), "project_context": "ctx"})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def status(jid):
    r = c.get(f"{B}/jobs/{jid}")
    return r.json()["status"] if r.status_code == 200 else r.status_code


def wait(cond, timeout=40, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def wait_status(jid, wanted, timeout=40):
    wanted = (wanted,) if isinstance(wanted, str) else wanted
    wait(lambda: status(jid) in wanted, timeout, f"{jid} -> {wanted}")


def idle():
    q = c.get(f"{B}/queue").json()
    if q["running"]:
        c.post(f"{B}/jobs/{q['running']['job_id']}/stop")
    for j in q["queued"]:
        c.post(f"{B}/queue/{j['job_id']}/cancel")
    wait(lambda: c.get(f"{B}/queue").json()["running"] is None, 30, "idle")


def gone(jid):
    db = SRV.db()
    return (c.get(f"{B}/jobs/{jid}").status_code == 404 and db.jobs.count_documents({"id": jid}) == 0
            and db.steps.count_documents({"job_id": jid}) == 0)


# ------------------------------------------------------------------ single delete
def test_delete_unknown_404():
    r = c.delete(f"{B}/jobs/does-not-exist")
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_delete_done_job_removes_job_steps_artifacts():
    jid = create(n=3)
    wait_status(jid, "done")
    assert SRV.db().steps.count_documents({"job_id": jid}) == 3
    r = c.delete(f"{B}/jobs/{jid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"deleted": True, "job_id": jid, "previous_status": "done", "steps_deleted": 3, "was_queued": False}
    assert gone(jid)
    for path in ("", "/steps/1", "/transcript", "/files", "/download", "/clone-source"):
        assert c.get(f"{B}/jobs/{jid}{path}").status_code == 404, path
    assert jid not in [j["job_id"] for j in c.get(f"{B}/jobs?limit=200").json()]
    assert c.delete(f"{B}/jobs/{jid}").status_code == 404  # second delete
    assert c.post(f"{B}/jobs/{jid}/resume").status_code == 404
    assert c.post(f"{B}/jobs/{jid}/restart").status_code == 404


def test_delete_running_409_then_after_stop_ok():
    idle()
    jid = create("stub-slow", 3)
    wait_status(jid, "running")
    r = c.delete(f"{B}/jobs/{jid}")
    assert r.status_code == 409 and "stop it first" in r.json()["detail"], r.text
    assert status(jid) == "running"
    c.post(f"{B}/jobs/{jid}/stop")
    wait_status(jid, "stopped")
    assert c.delete(f"{B}/jobs/{jid}").status_code == 200 and gone(jid)


def test_delete_queued_and_paused_renumbers_queue():
    idle()
    run = create("stub-slow", 3)
    wait_status(run, "running")
    q = [create(title=f"q{i}") for i in range(4)]
    assert c.post(f"{B}/queue/{q[3]}/pause").status_code == 200
    r = c.delete(f"{B}/jobs/{q[1]}")
    assert r.status_code == 200 and r.json()["previous_status"] == "queued" and r.json()["was_queued"], r.text
    r = c.delete(f"{B}/jobs/{q[3]}")
    assert r.status_code == 200 and r.json()["previous_status"] == "paused", r.text
    queue = c.get(f"{B}/queue").json()
    assert [j["job_id"] for j in queue["queued"]] == [q[0], q[2]]
    assert [j["queue_position"] for j in queue["queued"]] == [1, 2] and queue["count"] == 2
    assert gone(q[1]) and gone(q[3])
    # deleted jobs never start; the remaining ones run in order
    c.post(f"{B}/jobs/{run}/stop")
    wait_status(q[2], "done", 60)
    assert status(q[0]) == "done" and status(q[1]) == 404 and status(q[3]) == 404


def test_concurrent_delete_one_wins():
    jid = create()
    wait_status(jid, "done")
    with cf.ThreadPoolExecutor(6) as ex:
        codes = sorted(ex.map(lambda _: httpx.delete(f"{B}/jobs/{jid}", timeout=30).status_code, range(6)))
    assert codes == [200] + [404] * 5, codes


# ------------------------------------------------------------------ cancel vs delete
def test_queue_cancel_keeps_job_and_deprecated_alias():
    idle()
    run = create("stub-slow", 3)
    wait_status(run, "running")
    a, b = create(title="ca"), create(title="cb")
    r = c.post(f"{B}/queue/{a}/cancel")
    assert r.status_code == 200 and r.json() == {"job_id": a, "status": "cancelled", "queue_position": None}, r.text
    assert status(a) == "cancelled"  # kept in history
    r = c.delete(f"{B}/queue/{b}")  # deprecated alias, same behaviour
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.headers.get("deprecation") == "true" and "/cancel" in r.headers.get("link", "")
    assert status(b) == "cancelled"
    assert c.post(f"{B}/queue/{a}/cancel").status_code == 409  # not in the queue any more
    assert c.post(f"{B}/queue/nope/cancel").status_code == 404
    assert c.post(f"{B}/queue/{run}/cancel").status_code == 409  # running: use stop
    assert c.delete(f"{B}/jobs/{a}").json()["previous_status"] == "cancelled" and gone(a)
    c.post(f"{B}/jobs/{run}/stop")
    wait_status(run, "stopped")


# ------------------------------------------------------------------ bulk
def test_bulk_delete_mixed():
    idle()
    done1, done2 = create(), create()
    wait_status(done2, "done"); wait_status(done1, "done")
    run = create("stub-slow", 3)
    wait_status(run, "running")
    queued = create(title="bq")
    r = c.post(f"{B}/jobs/bulk-delete", json={"job_ids": [done1, done2, done1, run, queued, "missing", " "]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == [done1, done2, queued] and body["deleted_count"] == 3
    assert body["skipped"] == [{"job_id": run, "reason": "running"}, {"job_id": "missing", "reason": "not_found"}]
    assert gone(done1) and gone(done2) and gone(queued) and status(run) == "running"
    assert c.get(f"{B}/queue").json()["count"] == 0
    c.post(f"{B}/jobs/{run}/stop")
    wait_status(run, "stopped")


def test_bulk_delete_validation():
    for body in ({}, {"job_ids": []}, {"job_ids": ["  "]}, {"job_ids": "abc"}, {"job_ids": ["x"] * 501}):
        r = c.post(f"{B}/jobs/bulk-delete", json=body)
        assert r.status_code == 422, (body if len(str(body)) < 80 else "501 ids", r.status_code)


# ------------------------------------------------------------------ delete all finished
def test_delete_finished_only_finished():
    idle()
    done = create()
    err = create("stub-503")
    wait_status(done, "done"); wait_status(err, "error")
    stopped = create("stub-slow", 3)
    wait_status(stopped, "running")
    c.post(f"{B}/jobs/{stopped}/stop")
    wait_status(stopped, "stopped")
    run = create("stub-slow", 3)
    wait_status(run, "running")
    queued, paused, cancelled = create(title="fq"), create(title="fp"), create(title="fc")
    c.post(f"{B}/queue/{paused}/pause")
    c.post(f"{B}/queue/{cancelled}/cancel")
    before = {j["job_id"]: j["status"] for j in c.get(f"{B}/jobs?limit=200").json()}
    finished = {k for k, v in before.items() if v in ("done", "error", "stopped", "cancelled")}
    assert {done, err, stopped, cancelled} <= finished
    r = c.post(f"{B}/jobs/delete-finished")
    assert r.status_code == 200, r.text
    assert set(r.json()["deleted"]) == finished and r.json()["deleted_count"] == len(finished) and r.json()["skipped"] == []
    left = {j["job_id"]: j["status"] for j in c.get(f"{B}/jobs?limit=200").json()}
    assert left == {run: "running", queued: "queued", paused: "paused"}, left
    assert SRV.db().steps.count_documents({"job_id": {"$in": list(finished)}}) == 0
    q = c.get(f"{B}/queue").json()
    assert [(j["job_id"], j["queue_position"]) for j in q["queued"]] == [(queued, 1), (paused, 2)]
    r = c.post(f"{B}/jobs/delete-finished")
    assert r.json()["deleted_count"] == 0
    idle()


def test_restart_link_to_deleted_source_is_harmless():
    src = create()
    wait_status(src, "done")
    r = c.post(f"{B}/jobs/{src}/restart")
    assert r.status_code == 201, r.text
    new = r.json()["job_id"]
    wait_status(new, "done")
    assert c.delete(f"{B}/jobs/{src}").status_code == 200
    j = c.get(f"{B}/jobs/{new}").json()
    assert j["status"] == "done" and j["restarted_from"] == src  # link kept, source gone
    assert c.post(f"{B}/jobs/{new}/restart").status_code == 201  # restarting the copy still works


if __name__ == "__main__":
    SRV = IsolatedServer()
    B = SRV.__enter__()
    try:
        code = run_tests(globals())
    finally:
        SRV.__exit__(None, None, None)
    sys.exit(code)
