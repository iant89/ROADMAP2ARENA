"""Backend-tester extra checks for PR #6 (job deletion) and PR #7 (notifications).

Everything runs against a PRIVATE backend started by isolated_server.py (127.0.0.1, free port,
throwaway DB dropped on exit). Webhooks go to local HTTP receivers and email to local aiosmtpd
sinks, all bound to 127.0.0.1 inside this process; all of them stop when the run ends.
Run one section per process (each < 2 min):
    /app/venv/bin/python test_delete_notify_extra.py del
    /app/venv/bin/python test_delete_notify_extra.py notif
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

from isolated_server import IsolatedServer, free_port

STUB = "http://127.0.0.1:9090"
B = ""
SRV: IsolatedServer | None = None
SUFFIX = secrets.token_hex(3)
PW = f"Pw-{secrets.token_hex(6)}"       # sentinel SMTP password, must never appear in any response
WRONG_PW = f"Wrong-{secrets.token_hex(4)}"
HOOKS: list[dict] = []
MAILS: list[dict] = []
W: dict = {}
SMTP: dict = {}
BODIES: list[str] = []                     # every response body seen (password leak check)
FINISHED = ("done", "error", "stopped", "cancelled")
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


class Client:
    def __init__(self):
        self.c = httpx.Client(timeout=40)

    def req(self, method, path, **kw):
        r = self.c.request(method, f"{B}{path}", **kw)
        BODIES.append(r.text)
        if r.headers.get("content-type", "").startswith("application/json"):
            no_id(r.json())
        return r

    def get(self, p, **kw): return self.req("GET", p, **kw)
    def post(self, p, **kw): return self.req("POST", p, **kw)
    def put(self, p, **kw): return self.req("PUT", p, **kw)
    def delete(self, p, **kw): return self.req("DELETE", p, **kw)


c = Client()


def no_id(o, path="$"):
    if isinstance(o, dict):
        assert "_id" not in o, f"_id leaked at {path}"
        for k, v in o.items():
            no_id(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            no_id(v, f"{path}[{i}]")


def has_key(o, key):
    if isinstance(o, dict):
        return key in o or any(has_key(v, key) for v in o.values())
    if isinstance(o, list):
        return any(has_key(v, key) for v in o)
    return False


def parallel(n, fn):
    out, bar = [None] * n, threading.Barrier(n)

    def go(i):
        cl = httpx.Client(timeout=40)
        bar.wait()
        r = fn(cl, i)
        BODIES.append(r.text)
        out[i] = r
    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def roadmap(n, tag):
    return f"# test_{SUFFIX}_{tag}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def mk(model, n, tag):
    r = c.post("/jobs", json={"arena_url": STUB, "model": model, "roadmap_md": roadmap(n, tag)})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def job(jid):
    r = c.get(f"/jobs/{jid}")
    assert r.status_code == 200, (jid, r.status_code)
    return r.json()


def wait(jid, timeout=40, statuses=FINISHED):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/jobs/{jid}")
        if r.status_code == 404:
            return None
        if r.json()["status"] in statuses:
            return r.json()
        time.sleep(0.2)
    raise AssertionError(f"{jid} not in {statuses}")


def wait_idle(timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        q = c.get("/queue").json()
        if q["running"] is None and q["waiting"] == 0:
            return q
        time.sleep(0.2)
    raise AssertionError("queue not idle")


def wait_step_running(jid, idx=1, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if job(jid)["steps"][idx - 1]["status"] == "running":
            return
        time.sleep(0.1)
    raise AssertionError("step never running")


def dense():
    q = c.get("/queue").json()
    pos = [x["queue_position"] for x in q["queued"]]
    assert pos == list(range(1, len(pos) + 1)), pos
    return q


def orphan_steps(ids):
    return SRV.db().steps.count_documents({"job_id": {"$in": list(ids)}})


# ------------------------------------------------------------------ local sinks (127.0.0.1 only)
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/slow"):
            time.sleep(12)
        HOOKS.append({"path": self.path, "json": json.loads(body or b"{}"), "t": time.time()})
        code = 500 if self.path.startswith("/fail") else 200
        try:
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"boom" if code == 500 else b"ok")
        except OSError:
            pass

    def log_message(self, *a):
        pass


class MailHandler:
    def __init__(self, reject=False):
        self.reject = reject

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        if self.reject:
            return "554 Transaction failed (test sink)"
        MAILS.append({"to": envelope.rcpt_tos, "data": envelope.content.decode("utf8", "replace"), "t": time.time()})
        return "250 OK"


def authenticator(server, session, envelope, mechanism, auth_data):
    return AuthResult(success=(auth_data.login == b"r2a" and auth_data.password == PW.encode()), handled=False)


def start_sinks():
    httpd = ThreadingHTTPServer(("127.0.0.1", free_port()), Receiver)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    p = httpd.server_address[1]
    W.update(ok=f"http://127.0.0.1:{p}/hook", fail=f"http://127.0.0.1:{p}/fail", slow=f"http://127.0.0.1:{p}/slow",
             dead=f"http://127.0.0.1:{free_port()}/x")
    ctrls = [Controller(MailHandler(), hostname="127.0.0.1", port=free_port()),
             Controller(MailHandler(), hostname="127.0.0.1", port=free_port(), authenticator=authenticator,
                        auth_require_tls=False, auth_required=True),
             Controller(MailHandler(reject=True), hostname="127.0.0.1", port=free_port())]
    for ct in ctrls:
        ct.start()
    SMTP.update(plain=ctrls[0].port, auth=ctrls[1].port, reject=ctrls[2].port, dead=free_port())
    return httpd, ctrls


# ================================================================== PR #6 deletion
SUBS = ("", "/steps/1", "/transcript", "/transcript.html", "/files", "/files/download?path=app/main.py",
        "/download", "/clone-source")


def test_d_hard_delete_done_job():
    jid = mk("gpt-4o", 3, "hd")
    assert wait(jid)["status"] == "done"
    assert c.get(f"/jobs/{jid}/files").json()
    r = c.delete(f"/jobs/{jid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "job_id": jid, "previous_status": "done", "steps_deleted": 3,
                        "was_queued": False}, r.json()
    for sub in SUBS:
        assert c.get(f"/jobs/{jid}{sub}").status_code == 404, sub
    for act in ("resume", "restart", "stop"):
        assert c.post(f"/jobs/{jid}/{act}").status_code == 404, act
    assert c.post(f"/queue/{jid}/cancel").status_code == 404
    assert c.delete(f"/jobs/{jid}").status_code == 404
    assert all(x["job_id"] != jid for x in c.get("/jobs", params={"limit": 200}).json())
    assert orphan_steps([jid]) == 0 and SRV.db().jobs.count_documents({"id": jid}) == 0
    for bad in (str(uuid.uuid4()), "not-a-uuid", "x" * 300):
        r = c.delete(f"/jobs/{bad}")
        assert r.status_code == 404 and "detail" in r.json()
    assert c.req("PATCH", f"/jobs/{str(uuid.uuid4())}").status_code == 405
    assert c.get("/jobs/bulk-delete").status_code in (404, 405)  # GET hits /jobs/{id} -> unknown job
    assert c.get("/jobs/delete-finished").status_code in (404, 405)
    assert c.req("PUT", "/jobs/bulk-delete", json={"job_ids": ["a"]}).status_code == 405


def test_d_running_409_and_bulk_skip():
    jid = mk("stub-slow", 2, "run")
    wait_step_running(jid)
    r = c.delete(f"/jobs/{jid}")
    assert r.status_code == 409 and "stop" in r.json()["detail"], r.text
    r = c.post("/jobs/bulk-delete", json={"job_ids": [jid]})
    assert r.status_code == 200 and r.json() == {"deleted": [], "deleted_count": 0,
                                                 "skipped": [{"job_id": jid, "reason": "running"}]}, r.json()
    assert job(jid)["status"] == "running"
    S["running"] = jid


def test_d_queued_paused_delete_and_cancel_alias():
    blocker = S["running"]
    q1, q2, q3, q4, q5 = (mk("gpt-4o", 1, f"q{i}") for i in range(1, 6))
    assert c.post(f"/queue/{q1}/pause").status_code == 200
    r = c.delete(f"/jobs/{q2}")
    assert r.status_code == 200 and r.json()["previous_status"] == "queued" and r.json()["was_queued"] is True
    assert r.json()["steps_deleted"] == 1
    r = c.delete(f"/jobs/{q1}")
    assert r.status_code == 200 and r.json()["previous_status"] == "paused" and r.json()["was_queued"] is True
    q = dense()
    assert [x["job_id"] for x in q["queued"]] == [q3, q4, q5]
    # cancel vs deprecated alias
    r = c.post(f"/queue/{q4}/cancel")
    assert r.status_code == 200 and r.json() == {"job_id": q4, "status": "cancelled", "queue_position": None}
    assert "deprecation" not in {k.lower() for k in r.headers}
    r = c.delete(f"/queue/{q5}")
    assert r.status_code == 200 and r.json() == {"job_id": q5, "status": "cancelled", "queue_position": None}
    assert r.headers.get("deprecation") == "true", dict(r.headers)
    assert r.headers.get("link") == f'</api/queue/{q5}/cancel>; rel="successor-version"', r.headers.get("link")
    for jid in (q4, q5):
        assert job(jid)["status"] == "cancelled"  # cancel keeps the job
    assert c.post(f"/queue/{q4}/cancel").status_code == 409
    assert c.delete(f"/queue/{q5}").status_code == 409
    assert c.post(f"/queue/{blocker}/cancel").status_code == 409
    assert c.post(f"/queue/{uuid.uuid4()}/cancel").status_code == 404
    assert c.get(f"/queue/{q4}/cancel").status_code == 405
    q = dense()
    assert [x["job_id"] for x in q["queued"]] == [q3]
    r = c.delete(f"/jobs/{q4}")
    assert r.status_code == 200 and r.json()["previous_status"] == "cancelled" and r.json()["was_queued"] is False
    assert wait(blocker)["status"] == "done" and wait(q3)["status"] == "done"
    for jid in (q1, q2):
        assert c.get(f"/jobs/{jid}").status_code == 404
    assert orphan_steps([q1, q2, q4]) == 0
    S["cancelled"] = q5
    S["done_ids"] = [blocker, q3]


def test_d_bulk_mixed_and_validation():
    run = mk("stub-slow", 1, "bulkrun")
    wait_step_running(run)
    d1, d2 = S["done_ids"]
    unk = str(uuid.uuid4())
    body = {"job_ids": [d1, unk, run, f" {d2} ", d1, "  ", "not-a-uuid", S["cancelled"]]}
    r = c.post("/jobs/bulk-delete", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["deleted"] == [d1, d2, S["cancelled"]] and d["deleted_count"] == 3, d
    assert d["skipped"] == [{"job_id": unk, "reason": "not_found"}, {"job_id": run, "reason": "running"},
                            {"job_id": "not-a-uuid", "reason": "not_found"}], d["skipped"]
    assert orphan_steps([d1, d2, S["cancelled"]]) == 0
    for bad in ({}, {"job_ids": []}, {"job_ids": [""]}, {"job_ids": ["  ", ""]}, {"job_ids": "abc"},
                {"job_ids": [1, 2]}, {"job_ids": None}, {"job_ids": [str(uuid.uuid4())] * 501},
                {"job_ids": [{"id": d1}]}, [d1], "x"):
        r = c.post("/jobs/bulk-delete", json=bad)
        assert r.status_code == 422, (str(bad)[:60], r.status_code, r.text[:100])
    r = c.post("/jobs/bulk-delete", content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = c.post("/jobs/bulk-delete", json={"job_ids": [str(uuid.uuid4()) for _ in range(500)]})
    assert r.status_code == 200 and r.json()["deleted_count"] == 0 and len(r.json()["skipped"]) == 500
    wait(run)
    S["leftover"] = [run]


def test_d_delete_finished_only_finished():
    """Isolated DB only: this endpoint deletes every finished job."""
    done = S["leftover"][0]
    err = mk("stub-503", 1, "dferr")
    wait(err)
    stopped = mk("stub-slow", 2, "dfstop")
    wait_step_running(stopped)
    assert c.post(f"/jobs/{stopped}/stop").status_code == 200
    running = mk("stub-slow", 1, "dfrun")
    wait_step_running(running)
    queued = mk("gpt-4o", 1, "dfq")
    paused = mk("gpt-4o", 1, "dfp")
    canc = mk("gpt-4o", 1, "dfc")
    assert c.post(f"/queue/{paused}/pause").status_code == 200
    assert c.post(f"/queue/{canc}/cancel").status_code == 200
    statuses = {j: job(j)["status"] for j in (done, err, stopped, running, queued, paused, canc)}
    assert statuses == {done: "done", err: "error", stopped: "stopped", running: "running", queued: "queued",
                        paused: "paused", canc: "cancelled"}, statuses
    finished_in_db = {j["id"] for j in SRV.db().jobs.find({"status": {"$in": list(FINISHED)}}, {"id": 1})}
    r = c.post("/jobs/delete-finished")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"deleted", "deleted_count", "skipped"}
    assert set(d["deleted"]) == finished_in_db and {done, err, stopped, canc} <= set(d["deleted"]), d
    assert d["skipped"] == [] and d["deleted_count"] == len(d["deleted"])
    for j in (running, queued, paused):
        assert c.get(f"/jobs/{j}").status_code == 200, j
    assert orphan_steps(d["deleted"]) == 0
    assert c.post("/jobs/delete-finished").json()["deleted_count"] == 0
    dense()
    assert c.delete(f"/jobs/{paused}").status_code == 200
    wait(running)
    wait(queued)
    wait_idle()


def test_d_race_double_delete():
    jid = mk("gpt-4o", 1, "dbl")
    wait(jid)
    res = parallel(6, lambda cl, i: cl.delete(f"{B}/jobs/{jid}"))
    codes = sorted(r.status_code for r in res)
    assert codes == [200, 404, 404, 404, 404, 404], codes
    assert orphan_steps([jid]) == 0


def test_d_race_delete_vs_start():
    outcomes = []
    import os
    for offset in [float(x) for x in os.environ.get("DVS_OFFSETS", "4.8,5.0,5.1").split(",")]:
        blocker = mk("stub-slow", 1, "dvsb")
        wait_step_running(blocker)
        q = mk(os.environ.get("DVS_QMODEL", "gpt-4o"), 1, "dvsq")
        time.sleep(offset - 0.3)
        res = parallel(4, lambda cl, i: cl.delete(f"{B}/jobs/{q}"))
        codes = sorted(r.status_code for r in res)
        assert all(x in (200, 404, 409) for x in codes), codes
        if 200 in codes:
            assert codes.count(200) == 1 and c.get(f"/jobs/{q}").status_code == 404
            prev = next(r for r in res if r.status_code == 200).json()["previous_status"]
            assert orphan_steps([q]) == 0
            outcome = f"deleted ({prev})"
        else:
            assert set(codes) == {409}, codes
            assert wait(q)["status"] == "done"
            outcome = "started"
        wait_idle()
        q_ = dense()
        assert q_["running"] is None and q_["count"] == 0
        outcomes.append((offset, codes, outcome))
        for j in (blocker, q):
            if c.get(f"/jobs/{j}").status_code == 200:
                assert c.delete(f"/jobs/{j}").status_code == 200
    print("    delete-vs-start outcomes:", outcomes)


def test_d_race_delete_during_bulk():
    ids = [mk("gpt-4o", 1, f"bulk{i}") for i in range(12)]
    for j in ids:
        wait(j)

    def fn(cl, i):
        if i == 0:
            return cl.post(f"{B}/jobs/bulk-delete", json={"job_ids": ids})
        if i == 1:
            return cl.post(f"{B}/jobs/bulk-delete", json={"job_ids": ids[6:]})
        if i == 2:
            return cl.post(f"{B}/jobs/delete-finished")
        return cl.delete(f"{B}/jobs/{ids[i - 3]}")
    res = parallel(9, fn)
    assert all(r.status_code in (200, 404) for r in res), [r.status_code for r in res]
    deleted = []
    for r in res[:3]:
        assert r.status_code == 200
        deleted += r.json()["deleted"]
        assert all(s["reason"] == "not_found" for s in r.json()["skipped"])
    deleted += [ids[i - 3] for i in range(3, 9) if res[i].status_code == 200]
    mine = [d for d in deleted if d in ids]
    assert sorted(mine) == sorted(ids), "each id must be deleted exactly once"
    for j in ids:
        assert c.get(f"/jobs/{j}").status_code == 404
    assert orphan_steps(ids) == 0


# ================================================================== PR #7 notifications
NS = "/notifications/settings"


def nput(body, code=200):
    r = c.put(NS, json=body)
    assert r.status_code == code, (str(body)[:80], r.status_code, r.text[:200])
    return r.json()


def items():
    return c.get("/notifications", params={"limit": 200}).json()["items"]


def wait_for(pred, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    return pred()


def test_n_settings_shape_and_validation():
    s = c.get(NS).json()
    assert set(s) == {"app_url", "in_app", "webhook", "email", "updated_at"}, set(s)
    assert set(s["email"]) == {"enabled", "host", "port", "security", "username", "from_addr", "to_addrs", "events",
                               "password_set", "password_masked"}, set(s["email"])
    assert not has_key(s, "password") and s["email"]["password_set"] is False
    before = c.get(NS).json()
    bad = [{"bogus": 1}, {"app_url": "ftp://x"}, {"app_url": ""}, {"app_url": 5}, {"webhook": {"url": "ftp://x"}},
           {"webhook": {"enabled": True}}, {"webhook": {"enabled": "yes", "url": W["ok"]}}, {"webhook": "str"},
           {"webhook": {"events": {"bogus": True}}}, {"webhook": {"events": {"job_done": "yes"}}},
           {"webhook": {"events": []}}, {"webhook": {"extra": 1}}, {"in_app": {"enabled": True}},
           {"in_app": {"events": {"job_done": 1}}}, {"email": {"port": 0}}, {"email": {"port": 65536}},
           {"email": {"port": "25"}}, {"email": {"port": 25.5}}, {"email": {"port": True}},
           {"email": {"security": "tls"}}, {"email": {"from_addr": "bad"}}, {"email": {"to_addrs": ["a@b.co", "bad"]}},
           {"email": {"to_addrs": 5}}, {"email": {"to_addrs": [1]}}, {"email": {"enabled": True}},
           {"email": {"host": "bad host!"}}, {"email": {"host": 5}}, {"email": {"username": None}},
           {"email": {"password": 123}}, {"email": {"password": PW, "port": 0}},
           {"email": {"password": PW, "bogus": 1}}, [], "x", None, [{"email": {"password": PW}}]]
    for body in bad:
        r = c.put(NS, content=json.dumps(body), headers={"content-type": "application/json"})
        assert r.status_code == 422, (str(body)[:80], r.status_code, r.text[:150])
        assert "detail" in r.json()
    r = c.put(NS, content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert c.get(NS).json() == before, "rejected PUTs changed settings"
    assert c.post(NS).status_code == 405


def test_n_settings_wrong_method_405():
    r = c.delete(NS)
    assert r.status_code == 405, (r.status_code, r.text)


def test_n_password_write_only():
    s = nput({"email": {"host": "127.0.0.1", "port": SMTP["auth"], "security": "none", "username": "r2a",
                        "password": PW, "from_addr": "r2a@test.local", "to_addrs": "a@test.local; b@test.local"}})
    assert not has_key(s, "password") and s["email"]["password_set"] is True and s["email"]["password_masked"] == "********"
    assert s["email"]["to_addrs"] == ["a@test.local", "b@test.local"]
    assert c.get(NS).json() == s
    n0 = len(MAILS)
    assert c.post("/notifications/test/email").json()["ok"] is True       # saved password works
    nput({"email": {"port": SMTP["auth"]}})                                 # omitted -> kept
    assert c.post("/notifications/test/email").json()["ok"] is True
    nput({"email": {"password": "********"}})                               # mask -> kept
    r = c.post("/notifications/test/email").json()
    assert r["ok"] is True and r["to_addrs"] == ["a@test.local", "b@test.local"], r
    r = c.post("/notifications/test/email", json={"password": WRONG_PW}).json()   # unsaved override
    assert r["ok"] is False and "authentication failed" in r["error"], r
    assert c.get(NS).json()["email"]["password_set"] is True
    assert c.post("/notifications/test/email").json()["ok"] is True       # override not saved
    nput({"email": {"password": ""}})
    assert c.get(NS).json()["email"]["password_set"] is False and c.get(NS).json()["email"]["password_masked"] == ""
    r = c.post("/notifications/test/email").json()
    assert r["ok"] is False, r                                              # AUTH required, no password now
    nput({"email": {"password": PW}})
    assert len(MAILS) >= n0 + 4


def test_n_test_buttons():
    nput({"webhook": {"url": W["ok"]}})
    n = len(HOOKS)
    r = c.post("/notifications/test/webhook")
    assert r.status_code == 200 and r.json() == {"ok": True, "status_code": 200, "error": None, "url": W["ok"]}, r.text
    h = HOOKS[n]["json"]
    assert set(h) == {"event", "job_id", "title", "status", "step", "message", "url", "link", "timestamp", "app", "text", "content"}
    assert h["event"] == "test" and h["job_id"] is None and ISO_Z.match(h["timestamp"])
    r = c.post("/notifications/test/webhook", json={"url": W["fail"]}).json()
    assert r["ok"] is False and r["status_code"] == 500 and "HTTP 500" in r["error"], r
    r = c.post("/notifications/test/webhook", json={"url": W["dead"]}).json()
    assert r["ok"] is False and r["status_code"] is None and r["error"], r
    t = time.time()
    r = c.post("/notifications/test/webhook", json={"url": W["slow"]}).json()
    assert r["ok"] is False and "timed out" in r["error"] and time.time() - t < 13, (r, time.time() - t)
    assert c.get(NS).json()["webhook"]["url"] == W["ok"]                    # override not saved
    for body in ({"url": "ftp://x"}, {"url": 5}, {"bogus": 1}):
        assert c.post("/notifications/test/webhook", json=body).status_code == 422, body
    nput({"webhook": {"url": ""}})
    assert c.post("/notifications/test/webhook").status_code == 422
    nput({"webhook": {"url": W["ok"]}})
    # email to the plain sink with unsaved overrides
    n = len(MAILS)
    over = {"host": "127.0.0.1", "port": SMTP["plain"], "security": "none", "username": "", "from_addr": "x@test.local",
            "to_addrs": ["z@test.local"]}
    r = c.post("/notifications/test/email", json=over).json()
    assert r == {"ok": True, "error": None, "to_addrs": ["z@test.local"]}, r
    m = MAILS[n]
    assert m["to"] == ["z@test.local"] and "Subject: [ROADMAP2ARENA] Test notification" in m["data"]
    assert c.get(NS).json()["email"]["port"] == SMTP["auth"]                 # not saved
    r = c.post("/notifications/test/email", json={**over, "security": "starttls"}).json()
    assert r["ok"] is False and "STARTTLS" in r["error"], r
    r = c.post("/notifications/test/email", json={**over, "port": SMTP["dead"]}).json()
    assert r["ok"] is False and r["error"], r
    r = c.post("/notifications/test/email", json={**over, "port": SMTP["reject"]}).json()
    assert r["ok"] is False and "554" in r["error"], r
    for body in ({"port": 0}, {"security": "x"}, {"to_addrs": ["bad"]}, {"bogus": 1}):
        assert c.post("/notifications/test/email", json=body).status_code == 422, body


def channels_on(webhook=W, email_port=None):
    nput({"in_app": {"events": {e: True for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}},
          "webhook": {"enabled": True, "url": W["ok"], "events": {e: True for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}},
          "email": {"enabled": True, "host": "127.0.0.1", "port": email_port or SMTP["plain"], "security": "none",
                    "username": "", "from_addr": "r2a@test.local", "to_addrs": ["ops@test.local"],
                    "events": {e: True for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}}})


def hooks_for(jid=None, event=None, since=0):
    return [h["json"] for h in HOOKS[since:] if (jid is None or h["json"]["job_id"] == jid)
            and (event is None or h["json"]["event"] == event)]


def test_n_events_fire():
    channels_on()
    c.delete("/notifications")
    h0, m0 = len(HOOKS), len(MAILS)
    jid = mk("gpt-4o", 2, "evdone")
    wait(jid)
    assert wait_for(lambda: len(hooks_for(since=h0)) >= 2)
    time.sleep(1.0)
    evs = [h["event"] for h in hooks_for(since=h0)]
    assert sorted(evs) == ["job_done", "queue_empty"], evs
    p = hooks_for(jid, "job_done", h0)[0]
    assert p["step"] == 2 and p["status"] == "done" and jid in p["url"] and p["title"] == f"test_{SUFFIX}_evdone"
    assert p["text"] == p["content"] and p["message"] in p["text"]
    assert len(MAILS) - m0 == 2 and any("Job finished: test_" in m["data"] for m in MAILS[m0:])
    its = items()
    assert sorted(i["event"] for i in its) == ["job_done", "queue_empty"]
    nd = next(i for i in its if i["event"] == "job_done")
    assert set(nd) == {"id", "event", "job_id", "title", "status", "step", "message", "url", "link", "created_at", "read", "deliveries"}
    assert wait_for(lambda: all(k in next(i for i in items() if i["event"] == "job_done")["deliveries"] for k in ("webhook", "email")))
    nd = next(i for i in items() if i["event"] == "job_done")
    assert nd["deliveries"]["webhook"]["ok"] and nd["deliveries"]["email"]["ok"], nd["deliveries"]
    assert any(e["msg"] == "Notification: webhook delivered (HTTP 200)" for e in job(jid)["log"])
    # failed + stopped
    h0 = len(HOOKS)
    e = mk("stub-503", 2, "evfail")
    wait(e)
    assert wait_for(lambda: hooks_for(e, "job_failed", h0))
    p = hooks_for(e, "job_failed", h0)[0]
    assert p["step"] == 1 and "Step 1 failed:" not in p["message"] and "at step 1" in p["message"], p["message"]
    s = mk("stub-slow", 2, "evstop")
    wait_step_running(s)
    assert c.post(f"/jobs/{s}/stop").status_code == 200
    assert wait_for(lambda: hooks_for(s, "job_stopped", h0))
    p = hooks_for(s, "job_stopped", h0)[0]
    assert p["step"] == 1 and p["status"] == "stopped"
    time.sleep(1)
    assert len(hooks_for(e, None, h0)) == 1 and len(hooks_for(s, None, h0)) == 1   # once per run end


def test_n_queue_empty_only_when_drained():
    channels_on()
    time.sleep(0.5)
    h0 = len(HOOKS)
    a = mk("stub-slow", 1, "qeA")
    b, d, p = mk("gpt-4o", 1, "qeB"), mk("gpt-4o", 1, "qeC"), mk("gpt-4o", 1, "qeP")
    assert c.post(f"/queue/{p}/pause").status_code == 200
    for j in (a, b, d):
        wait(j, 30)
    wait_for(lambda: hooks_for(None, "queue_empty", h0))
    time.sleep(1.0)
    qe = hooks_for(None, "queue_empty", h0)
    assert len(qe) == 1, [h["event"] for h in hooks_for(since=h0)]
    assert "(1 paused job left)" in qe[0]["message"], qe[0]["message"]
    assert [h["json"]["event"] for h in HOOKS[h0:]][-1] == "queue_empty"
    assert c.delete(f"/jobs/{p}").status_code == 200


def test_n_toggles_off_silent():
    channels_on()
    off = {e: False for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}
    nput({"in_app": {"events": off}, "webhook": {"events": off}, "email": {"events": off}})
    time.sleep(0.5)
    c.delete("/notifications")
    h0, m0 = len(HOOKS), len(MAILS)
    j1 = mk("gpt-4o", 1, "offA")
    j2 = mk("stub-503", 1, "offB")
    wait(j1), wait(j2)
    time.sleep(2.5)
    assert len(HOOKS) == h0 and len(MAILS) == m0 and c.get("/notifications").json()["total"] == 0
    # in-app off for job_done but webhook on -> webhook fires, nothing stored
    nput({"webhook": {"events": {"job_done": True}}})
    j3 = mk("gpt-4o", 1, "offC")
    wait(j3)
    assert wait_for(lambda: hooks_for(j3, "job_done", h0))
    time.sleep(1)
    assert c.get("/notifications").json()["total"] == 0 and len(MAILS) == m0
    assert [h["event"] for h in hooks_for(since=h0)] == ["job_done"]


def test_n_failures_never_break_jobs():
    channels_on()
    nput({"webhook": {"url": W["slow"]}, "email": {"port": SMTP["dead"]}})
    t0 = time.time()
    a = mk("gpt-4o", 3, "slowA")
    b = mk("gpt-4o", 1, "slowB")
    ja, jb = wait(a, 20), wait(b, 20)
    assert ja["status"] == "done" and jb["status"] == "done"
    assert time.time() - t0 < 6, f"jobs took {time.time() - t0:.1f}s with a slow webhook"
    from datetime import datetime
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    assert ts(jb["started_at"]) - ts(ja["finished_at"]) < 2.0
    # deliveries are sequential (webhook then email), so the email result waits for the 10 s webhook timeout
    assert wait_for(lambda: any("Notification: webhook failed - timed out" in e["msg"] for e in job(a)["log"]), 14)
    assert wait_for(lambda: any("Notification: email failed" in e["msg"] and e["level"] == "warn" for e in job(a)["log"]), 5)
    # 500 webhook + wrong SMTP password + SMTP 554
    nput({"webhook": {"url": W["fail"]}, "email": {"port": SMTP["auth"], "username": "r2a", "password": WRONG_PW}})
    d = mk("gpt-4o", 2, "failA")
    assert wait(d, 20)["status"] == "done"
    assert wait_for(lambda: any("webhook failed - HTTP 500" in e["msg"] for e in job(d)["log"]))
    assert wait_for(lambda: any("email failed - authentication failed" in e["msg"] for e in job(d)["log"]))
    nput({"email": {"port": SMTP["reject"], "username": "", "password": ""}, "webhook": {"url": W["dead"]}})
    e = mk("stub-503", 1, "failB")
    assert wait(e, 20)["status"] == "error"
    assert wait_for(lambda: sum("Notification:" in x["msg"] and x["level"] == "warn" for x in job(e)["log"]) >= 2)
    j = job(e)
    assert j["error"].startswith("Step 1 failed") and j["status"] == "error"
    wait_idle()


def test_n_list_read_clear():
    nput({"webhook": {"enabled": False}, "email": {"enabled": False},
          "in_app": {"events": {e: True for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}}})
    c.delete("/notifications")
    for i in range(3):
        wait(mk("gpt-4o", 1, f"lst{i}"))
    assert wait_for(lambda: c.get("/notifications").json()["total"] >= 6)
    d = c.get("/notifications").json()
    assert set(d) == {"items", "unread_count", "total"} and d["unread_count"] == d["total"] == 6, d
    cs = [i["created_at"] for i in d["items"]]
    assert cs == sorted(cs, reverse=True)
    assert len(c.get("/notifications", params={"limit": 1}).json()["items"]) == 1
    for params in ({"limit": 0}, {"limit": 201}, {"limit": "abc"}, {"unread": "maybe"}):
        assert c.get("/notifications", params=params).status_code == 422, params
    nid = d["items"][0]["id"]
    r = c.post(f"/notifications/{nid}/read")
    assert r.status_code == 200 and r.json() == {"id": nid, "read": True, "unread_count": 5}
    assert c.post(f"/notifications/{nid}/read").json()["unread_count"] == 5   # repeat
    u = c.get("/notifications", params={"unread": "true"}).json()
    assert len(u["items"]) == 5 and all(not i["read"] for i in u["items"])
    for bad in ("nope", str(uuid.uuid4()), "x" * 300):
        assert c.post(f"/notifications/{bad}/read").status_code == 404
        assert c.delete(f"/notifications/{bad}").status_code == 404
    r = c.delete(f"/notifications/{nid}")
    assert r.status_code == 200 and r.json() == {"id": nid, "deleted": True}
    assert c.delete(f"/notifications/{nid}").status_code == 404
    ids = [i["id"] for i in c.get("/notifications").json()["items"]]
    res = parallel(6, lambda cl, i: cl.post(f"{B}/notifications/read-all") if i == 0 else
                   cl.delete(f"{B}/notifications") if i == 1 else cl.post(f"{B}/notifications/{ids[i - 2]}/read"))
    assert all(r.status_code in (200, 404) for r in res), [r.status_code for r in res]
    assert res[0].status_code == 200 and res[1].status_code == 200
    r = c.delete("/notifications")
    assert r.status_code == 200 and r.json()["unread_count"] == 0
    d = c.get("/notifications").json()
    assert d == {"items": [], "unread_count": 0, "total": 0}
    assert c.post("/notifications/read-all").json() == {"updated": 0, "unread_count": 0}
    assert c.put("/notifications").status_code == 405 and c.get("/notifications/read-all").status_code == 405


def test_n_password_never_in_any_response():
    # job logs, notifications, settings and every body seen in this run
    for j in c.get("/jobs", params={"limit": 200}).json():
        BODIES.append(c.get(f"/jobs/{j['job_id']}").text)
    leaks = [b[:120] for b in BODIES if PW in b or WRONG_PW in b]
    assert not leaks, f"password echoed in {len(leaks)} response(s): {leaks[:2]}"


S: dict = {}
SECTIONS = {"del": [n for n in list(globals()) if n.startswith("test_d_")],
            "notif": [n for n in list(globals()) if n.startswith("test_n_")]}

if __name__ == "__main__":
    section = sys.argv[1]
    names = SECTIONS[section] if len(sys.argv) < 3 else sys.argv[2:]
    httpd, ctrls = start_sinks()
    passed, failed = 0, []
    SRV = IsolatedServer()
    try:
        with SRV as base:
            B = base
            print(f"    isolated backend {base} db={SRV.db_name} pid={SRV.proc.pid}", flush=True)
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
            tb = open(SRV.log_path).read()
            print(f"    isolated server log: {tb.count('Traceback')} traceback(s), {len(tb.splitlines())} lines")
    finally:
        httpd.shutdown()
        for ct in ctrls:
            ct.stop()
        alive = SRV.proc.poll() is None if SRV.proc else False
        print(f"    sinks stopped; isolated backend exited={not alive} (code {SRV.proc.returncode if SRV.proc else None}); "
              f"db dropped={SRV.db_name not in SRV.db().client.list_database_names()}")
    print(f"\n{passed} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)
