"""Tests for notifications (PR feat/notifications): settings, in-app list, webhook and SMTP delivery.

Run: /app/venv/bin/python /app/backend/tests/test_notifications.py
Starts its own backend on a throwaway DB (isolated_server.py). Webhooks go to a local
HTTP receiver and email to local aiosmtpd servers (requirements-dev.txt) - nothing
leaves 127.0.0.1. Needs the arena stand-in on 127.0.0.1:9090.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

from isolated_server import IsolatedServer, free_port, run_tests

GITHUB_EVENTS = ("github_pushed", "github_pr_opened", "github_pr_merged", "github_pr_closed", "github_checks_passed",
                 "github_checks_failed")  # added in PR D
STUB = os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090")
B = ""
SRV: IsolatedServer | None = None
c = httpx.Client(timeout=30)
HOOKS: list[dict] = []
MAILS: list = []
W = {}  # receiver urls
SMTP = {}


# ------------------------------------------------------------------ local receivers
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        HOOKS.append({"path": self.path, "json": json.loads(body or b"{}"), "ua": self.headers.get("User-Agent")})
        code = 500 if self.path.startswith("/fail") else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"boom" if code == 500 else b"ok")

    def log_message(self, *a):
        pass


class MailHandler:
    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        MAILS.append({"from": envelope.mail_from, "to": envelope.rcpt_tos, "data": envelope.content.decode("utf8", "replace"),
                      "auth": getattr(session, "auth_data", None)})
        return "250 OK"


def authenticator(server, session, envelope, mechanism, auth_data):
    ok = auth_data.login == b"r2a" and auth_data.password == b"right-pass"
    return AuthResult(success=ok, handled=False)


def start_receivers():
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Receiver)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    W.update(ok=f"http://127.0.0.1:{port}/hook", fail=f"http://127.0.0.1:{port}/fail", dead=f"http://127.0.0.1:{free_port()}/x")
    plain = Controller(MailHandler(), hostname="127.0.0.1", port=free_port())
    plain.start()
    auth = Controller(MailHandler(), hostname="127.0.0.1", port=free_port(), authenticator=authenticator,
                      auth_require_tls=False, auth_required=True)
    auth.start()
    SMTP.update(plain=plain.port, auth=auth.port)
    return httpd, plain, auth


# ------------------------------------------------------------------ helpers
def put(body, code=200):
    r = c.put(f"{B}/notifications/settings", json=body)
    assert r.status_code == code, (body, r.status_code, r.text)
    return r.json()


def mk(model="gpt-4o", n=2, title="notif test"):
    r = c.post(f"{B}/jobs", json={"arena_url": STUB, "model": model, "project_context": "",
                                  "roadmap_md": f"# {title}\n\n" + "".join(f"### S{i}\nx\n" for i in range(1, n + 1))})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def wait(cond, timeout=40, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError(f"timed out: {what}")


def status(jid):
    return c.get(f"{B}/jobs/{jid}").json()["status"]


def notes(**kw):
    return c.get(f"{B}/notifications", params={"limit": 200, **kw}).json()


def of_job(jid, event):
    return [n for n in notes()["items"] if n["job_id"] == jid and n["event"] == event]


def reset_all():
    c.delete(f"{B}/notifications")
    HOOKS.clear(); MAILS.clear()
    put({"in_app": {"events": {e: True for e in ("job_done", "job_failed", "job_stopped", "queue_empty")}},
         "webhook": {"enabled": False, "url": "", "events": {"job_done": True, "job_failed": True, "job_stopped": True, "queue_empty": True}},
         "email": {"enabled": False, "host": "", "from_addr": "", "to_addrs": [], "username": "", "password": "", "security": "none"}})


# ------------------------------------------------------------------ settings
def test_settings_defaults_and_shape():
    s = c.get(f"{B}/notifications/settings").json()
    assert set(s) == {"app_url", "in_app", "webhook", "email", "updated_at"}
    assert set(s["email"]) == {"enabled", "host", "port", "security", "username", "from_addr", "to_addrs", "events",
                               "password_set", "password_masked"}
    assert "password" not in s["email"] and s["email"]["password_set"] is False
    assert s["webhook"]["enabled"] is False and s["email"]["port"] == 587 and s["email"]["security"] == "starttls"
    assert set(s["in_app"]["events"]) == {"job_done", "job_failed", "job_stopped", "queue_empty", *GITHUB_EVENTS}
    assert all(s["in_app"]["events"][e] and not s["email"]["events"][e] for e in GITHUB_EVENTS)  # PR D


def test_settings_validation():
    bad = [{"nope": 1}, {"app_url": "ftp://x"}, {"webhook": {"enabled": True, "url": ""}}, {"webhook": {"url": "notaurl"}},
           {"webhook": {"enabled": "yes"}}, {"email": {"port": 0}}, {"email": {"port": "25"}}, {"email": {"port": True}},
           {"email": {"security": "tls"}}, {"email": {"from_addr": "bad"}}, {"email": {"to_addrs": ["a@b.co", "nope"]}},
           {"email": {"enabled": True}}, {"in_app": {"events": {"job_exploded": True}}}, {"in_app": {"events": {"job_done": "y"}}},
           {"webhook": {"secret": "x"}}, {"email": {"host": "bad host!"}}, {"email": {"password": 5}}, {"webhook": []}]
    for body in bad:
        put(body, 422)
    s = put({"webhook": {"url": W["ok"]}, "in_app": {"events": {"queue_empty": False}}})
    assert s["webhook"]["url"] == W["ok"] and s["webhook"]["enabled"] is False
    assert s["in_app"]["events"] == {"job_done": True, "job_failed": True, "job_stopped": True, "queue_empty": False,
                                     **{e: True for e in GITHUB_EVENTS}}
    s = put({"email": {"to_addrs": "a@example.com, b@example.com;c@example.com"}})
    assert s["email"]["to_addrs"] == ["a@example.com", "b@example.com", "c@example.com"] and s["updated_at"]
    reset_all()


def test_password_write_only():
    s = put({"email": {"password": "s3cret-pw"}})
    assert s["email"]["password_set"] is True and s["email"]["password_masked"] == "********"
    raw = c.get(f"{B}/notifications/settings").text
    assert "s3cret-pw" not in raw and '"password"' not in raw
    assert SRV.db().notification_settings.find_one()["email"]["password"] == "s3cret-pw"
    put({"email": {"password": "********", "host": "smtp.example.com"}})  # mask = keep
    assert SRV.db().notification_settings.find_one()["email"]["password"] == "s3cret-pw"
    assert put({"email": {"password": ""}})["email"]["password_set"] is False  # "" = clear
    for path in ("/notifications", "/notifications/settings"):
        assert "s3cret" not in c.get(f"{B}{path}").text


# ------------------------------------------------------------------ test buttons
def test_test_webhook():
    reset_all()
    assert c.post(f"{B}/notifications/test/webhook").status_code == 422  # no url saved
    r = c.post(f"{B}/notifications/test/webhook", json={"url": W["ok"]}).json()  # unsaved url
    assert r["ok"] is True and r["status_code"] == 200 and r["error"] is None, r
    p = HOOKS[-1]["json"]
    assert {"event", "job_id", "title", "status", "step", "message", "url", "text", "content"} <= set(p)
    assert p["event"] == "test" and "Test notification" in p["text"] and p["content"] == p["text"]
    assert HOOKS[-1]["ua"] == "ROADMAP2ARENA-notifier"
    r = c.post(f"{B}/notifications/test/webhook", json={"url": W["fail"]}).json()
    assert r["ok"] is False and r["status_code"] == 500 and "HTTP 500" in r["error"], r
    r = c.post(f"{B}/notifications/test/webhook", json={"url": W["dead"]}).json()
    assert r["ok"] is False and r["status_code"] is None and r["error"], r
    assert c.post(f"{B}/notifications/test/webhook", json={"url": "nope"}).status_code == 422
    put({"webhook": {"url": W["ok"]}})
    assert c.post(f"{B}/notifications/test/webhook").json()["ok"] is True  # saved url, even though disabled


def test_test_email_plain_auth_and_errors():
    reset_all()
    base = {"host": "127.0.0.1", "port": SMTP["plain"], "security": "none", "from_addr": "r2a@example.com",
            "to_addrs": ["ian@example.com"]}
    r = c.post(f"{B}/notifications/test/email", json=base).json()
    assert r["ok"] is True and r["to_addrs"] == ["ian@example.com"], r
    m = MAILS[-1]
    assert m["to"] == ["ian@example.com"] and "Subject: [ROADMAP2ARENA] Test notification" in m["data"]
    r = c.post(f"{B}/notifications/test/email", json={**base, "port": SMTP["auth"], "username": "r2a", "password": "right-pass"}).json()
    assert r["ok"] is True, r
    r = c.post(f"{B}/notifications/test/email", json={**base, "port": SMTP["auth"], "username": "r2a", "password": "wrong"}).json()
    assert r["ok"] is False and "authentication failed" in r["error"], r
    r = c.post(f"{B}/notifications/test/email", json={**base, "security": "starttls"}).json()
    assert r["ok"] is False and "STARTTLS" in r["error"], r
    r = c.post(f"{B}/notifications/test/email", json={**base, "port": free_port()}).json()
    assert r["ok"] is False and r["error"], r
    r = c.post(f"{B}/notifications/test/email").json()  # nothing saved
    assert r["ok"] is False and "missing" in r["error"], r
    assert c.post(f"{B}/notifications/test/email", json={"port": 99999}).status_code == 422
    # saved password is used when the test body omits it
    put({"email": {**base, "port": SMTP["auth"], "username": "r2a", "password": "right-pass"}})
    r = c.post(f"{B}/notifications/test/email", json={"host": "127.0.0.1"}).json()
    assert r["ok"] is True, r


# ------------------------------------------------------------------ job events
def enable_all():
    put({"webhook": {"enabled": True, "url": W["ok"]},
         "email": {"enabled": True, "host": "127.0.0.1", "port": SMTP["plain"], "security": "none",
                   "from_addr": "r2a@example.com", "to_addrs": ["ian@example.com"],
                   "events": {"job_done": True, "job_failed": True, "job_stopped": True, "queue_empty": True}}})


def test_done_event_all_channels_and_queue_empty():
    reset_all(); enable_all()
    jid = mk(title="Done job")
    wait(lambda: status(jid) == "done")
    n = wait(lambda: of_job(jid, "job_done"), 10, "job_done notification")[0]
    assert n["read"] is False and n["title"] == "Done job" and n["status"] == "done" and n["step"] == 2
    assert n["message"] == "Job finished: Done job - 2/2 steps" and n["url"].endswith(f"/?tab=history&job={jid}")
    hook = wait(lambda: [h for h in HOOKS if h["json"]["event"] == "job_done"], 10, "webhook")[0]["json"]
    assert hook["job_id"] == jid and hook["status"] == "done" and hook["step"] == 2 and hook["text"].startswith("\u2705")
    assert wait(lambda: [m for m in MAILS if "Job finished: Done job" in m["data"]], 10, "email")
    wait(lambda: [h for h in HOOKS if h["json"]["event"] == "queue_empty"], 10, "queue_empty webhook")
    assert [x for x in notes()["items"] if x["event"] == "queue_empty"]
    n = wait(lambda: (of_job(jid, "job_done")[0]["deliveries"].get("email") and of_job(jid, "job_done")[0]), 10, "deliveries")
    assert n["deliveries"]["webhook"]["ok"] and n["deliveries"]["email"]["ok"]
    log = [l["msg"] for l in c.get(f"{B}/jobs/{jid}").json()["log"]]
    assert "Notification: webhook delivered (HTTP 200)" in log and "Notification: email delivered" in log


def test_failed_and_stopped_events():
    reset_all(); enable_all()
    err = mk("stub-503", title="Failing job")
    wait(lambda: status(err) == "error")
    n = wait(lambda: of_job(err, "job_failed"), 10, "job_failed")[0]
    assert n["step"] == 1 and n["message"].startswith("Job failed: Failing job at step 1 - arena2api") and "Step 1 failed" not in n["message"]
    assert wait(lambda: [h for h in HOOKS if h["json"]["event"] == "job_failed" and h["json"]["job_id"] == err], 10)
    slow = mk("stub-slow", 3, title="Stopped job")
    wait(lambda: status(slow) == "running")
    time.sleep(0.5)
    c.post(f"{B}/jobs/{slow}/stop")
    n = wait(lambda: of_job(slow, "job_stopped"), 15, "job_stopped")[0]
    assert n["step"] == 1 and "Job stopped: Stopped job at step 1 (0/3 steps done)" == n["message"], n
    assert wait(lambda: [m for m in MAILS if "[ROADMAP2ARENA] Job stopped: Stopped job" in m["data"]], 10)


def test_queue_empty_only_when_drained_and_paused_note():
    reset_all()
    a = mk("stub-slow", 1, title="first")
    wait(lambda: status(a) == "running")
    b, p = mk(title="second"), mk(title="parked")
    c.post(f"{B}/queue/{p}/pause")
    wait(lambda: status(b) == "done", 30)
    wait(lambda: of_job(b, "job_done"), 10)
    time.sleep(0.5)
    empties = [n for n in notes()["items"] if n["event"] == "queue_empty"]
    assert len(empties) == 1, empties  # not after "first" (second was waiting), once after "second"
    assert empties[0]["message"] == "Queue empty - all queued jobs have finished (1 paused job left)"
    assert of_job(a, "job_done") and empties[0]["created_at"] > of_job(a, "job_done")[0]["created_at"]
    c.post(f"{B}/queue/{p}/cancel")


def test_per_event_toggles():
    reset_all()
    put({"in_app": {"events": {"job_done": False, "queue_empty": False}},
         "webhook": {"enabled": True, "url": W["ok"], "events": {"job_done": True, "queue_empty": False}}})
    jid = mk(title="toggle job")
    wait(lambda: status(jid) == "done")
    wait(lambda: [h for h in HOOKS if h["json"].get("job_id") == jid], 10, "webhook still sent")
    time.sleep(0.8)
    assert of_job(jid, "job_done") == [] and notes()["items"] == []  # in-app off for both events
    assert not [h for h in HOOKS if h["json"]["event"] == "queue_empty"]  # webhook off for queue_empty


def test_failing_webhook_and_smtp_never_break_jobs():
    reset_all()
    put({"webhook": {"enabled": True, "url": W["fail"]},
         "email": {"enabled": True, "host": "127.0.0.1", "port": free_port(), "security": "none",
                   "from_addr": "r2a@example.com", "to_addrs": ["ian@example.com"]}})
    jid = mk(n=3, title="resilient")
    wait(lambda: status(jid) == "done")
    j = c.get(f"{B}/jobs/{jid}").json()
    assert j["steps_done"] == 3 and j["error"] is None
    n = wait(lambda: (of_job(jid, "job_done") and of_job(jid, "job_done")[0]["deliveries"].get("email") and of_job(jid, "job_done")[0]), 20)
    assert n["deliveries"]["webhook"] == {**n["deliveries"]["webhook"], "ok": False, "status_code": 500}
    assert n["deliveries"]["email"]["ok"] is False
    log = [l for l in c.get(f"{B}/jobs/{jid}").json()["log"] if l["msg"].startswith("Notification:")]
    assert any(l["level"] == "warn" and "webhook failed - HTTP 500" in l["msg"] for l in log), log
    assert any(l["level"] == "warn" and l["msg"].startswith("Notification: email failed") for l in log), log
    nxt = mk(title="still works")  # the queue keeps going
    wait(lambda: status(nxt) == "done")


# ------------------------------------------------------------------ list / read / clear
def test_list_read_clear():
    reset_all()
    for i in range(3):
        jid = mk(title=f"list {i}")
        wait(lambda: status(jid) == "done")
    wait(lambda: notes()["unread_count"] >= 4, 10)  # 3 done + >=1 queue_empty
    d = notes()
    assert d["total"] == len(d["items"]) and d["unread_count"] == d["total"]
    assert [x["created_at"] for x in d["items"]] == sorted((x["created_at"] for x in d["items"]), reverse=True)
    assert set(d["items"][0]) == {"id", "event", "job_id", "title", "status", "step", "message", "url", "link", "created_at", "read", "deliveries"}
    assert d["items"][0]["link"] is None  # only GitHub events carry a link
    first = d["items"][0]["id"]
    r = c.post(f"{B}/notifications/{first}/read").json()
    assert r["read"] is True and r["unread_count"] == d["total"] - 1
    assert first not in [x["id"] for x in notes(unread="true")["items"]]
    assert len(c.get(f"{B}/notifications", params={"limit": 2}).json()["items"]) == 2
    for bad in ({"limit": 0}, {"limit": 201}, {"unread": "maybe"}):
        assert c.get(f"{B}/notifications", params=bad).status_code == 422, bad
    assert c.post(f"{B}/notifications/nope/read").status_code == 404
    assert c.delete(f"{B}/notifications/nope").status_code == 404
    assert c.delete(f"{B}/notifications/{first}").json() == {"id": first, "deleted": True}
    r = c.post(f"{B}/notifications/read-all").json()
    assert r["unread_count"] == 0 and notes()["unread_count"] == 0
    r = c.delete(f"{B}/notifications").json()
    assert r["deleted"] == d["total"] - 1 and notes() == {"items": [], "unread_count": 0, "total": 0}


def test_deleting_job_keeps_its_notifications():
    reset_all()
    jid = mk(title="to delete")
    wait(lambda: status(jid) == "done")
    wait(lambda: of_job(jid, "job_done"), 10)
    assert c.delete(f"{B}/jobs/{jid}").status_code == 200
    assert of_job(jid, "job_done"), "notification history is kept after the job is deleted"


if __name__ == "__main__":
    receivers = start_receivers()
    SRV = IsolatedServer()
    B = SRV.__enter__()
    try:
        code = run_tests(globals())
    finally:
        SRV.__exit__(None, None, None)
        receivers[0].shutdown(); receivers[1].stop(); receivers[2].stop()
    sys.exit(code)
