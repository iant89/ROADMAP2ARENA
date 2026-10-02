"""Extra tests (backend tester) for the history endpoints on feat/history-panel:
transcript, transcript.html, files list, single-file download (incl. traversal), clone-source,
cloned_from. Run: TEST_BASE_URL=http://localhost:8080 /app/venv/bin/python test_history_extra.py [names]
Direct-port sanity checks use http://127.0.0.1:8001. Only the local stub (:9090) is used.
test_zz_cleanup cancels/stops leftovers, waits, resets settings and deletes only jobs created here.
"""
from __future__ import annotations

import html.parser
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
DIRECT = "http://127.0.0.1:8001/api"
STUB = "http://127.0.0.1:9090"
SUFFIX = secrets.token_hex(3)
CREATED: list[str] = []
S: dict = {}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
c = httpx.Client(timeout=30)
FINISHED = ("done", "error", "stopped", "cancelled")
XSS_TITLE = f"test_{SUFFIX} <script>alert(1)</script> & \"co\" 🚀"
XSS_CTX = "</style><script>alert(2)</script> & <img src=x onerror=alert(3)> <a href=\"javascript:x\">l</a>"
XSS_STEP = "Step <b>one</b> & <script>alert(4)</script>"
ROADMAP_XSS = f"# {XSS_TITLE}\n\n### {XSS_STEP}\nDetails <iframe src=//evil> & more.\n\n### Second step\nDo 2.\n\n### Third step\nDo 3.\n"
VOID = {"meta", "br", "hr", "img", "input", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


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


def get(path, base=None, **kw):
    r = c.get(f"{base or BASE}{path}", **kw)
    if r.headers.get("content-type", "").startswith("application/json"):
        no_id(r.json())
    return r


def job(jid):
    r = get(f"/jobs/{jid}")
    assert r.status_code == 200, r.text
    return r.json()


def create(body):
    r = c.post(f"{BASE}/jobs", json=body)
    if r.status_code == 201:
        CREATED.append(r.json()["job_id"])
    return r


def create_ok(model, n, tag, **extra):
    r = create({"arena_url": STUB, "model": model, "roadmap_md": roadmap(n, tag), **extra})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def wait(jid, timeout=40, statuses=FINISHED):
    end = time.time() + timeout
    while time.time() < end:
        j = job(jid)
        if j["status"] in statuses:
            return j
        time.sleep(0.3)
    raise AssertionError(f"{jid} not in {statuses} after {timeout}s")


class Balance(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.tags, self.attrs, self.text = [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [k for k, _ in attrs]
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [k for k, _ in attrs]

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
        else:
            self.stack.pop()

    def handle_data(self, d):
        self.text.append(d)


# ================================================================== setup
def test_preflight():
    q = get("/queue").json()
    assert q["running"] is None and q["count"] == 0, q
    s = get("/settings").json()
    S["orig"] = {k: s[k] for k in s["env_defaults"]}
    print(f"    original settings: {S['orig']} (env defaults equal: {S['orig'] == s['env_defaults']})")


def test_setup_xss_job():
    r = create({"arena_url": STUB, "model": "gpt-4o", "roadmap_md": ROADMAP_XSS, "project_context": XSS_CTX})
    assert r.status_code == 201, r.text
    S["j"] = r.json()["job_id"]
    j = wait(S["j"])
    assert j["status"] == "done", j["status"]


# ================================================================== transcript
def test_transcript_json_matches_steps():
    jid = S["j"]
    r = get(f"/jobs/{jid}/transcript")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    t = r.json()
    keys = {"job_id", "title", "model", "arena_url", "status", "created_at", "finished_at", "project_context",
            "step_total", "steps_done", "turns"}
    assert set(t) == keys, set(t) ^ keys
    j = job(jid)
    assert t["job_id"] == jid and t["title"] == XSS_TITLE and t["project_context"] == XSS_CTX
    assert (t["status"], t["model"], t["arena_url"], t["step_total"], t["steps_done"]) == ("done", "gpt-4o", STUB, 3, 3)
    assert t["created_at"] == j["created_at"] and t["finished_at"] == j["finished_at"]
    assert [x["step_index"] for x in t["turns"]] == [1, 2, 3]
    tk = {"step_index", "step_title", "status", "prompt", "response", "error", "artifact_paths", "started_at", "finished_at"}
    for turn, st in zip(t["turns"], j["steps"]):
        assert set(turn) == tk, set(turn) ^ tk
        full = get(f"/jobs/{jid}/steps/{turn['step_index']}").json()
        assert turn["step_title"] == st["title"] and turn["status"] == "done" and turn["error"] is None
        assert turn["prompt"] == full["prompt"] and turn["response"] == full["response"]
        assert turn["artifact_paths"] == st["artifact_paths"]
        assert turn["started_at"] == full["started_at"] and ISO_Z.match(turn["finished_at"])
    assert t["turns"][0]["step_title"] == XSS_STEP
    # same via the direct port
    assert get(f"/jobs/{jid}/transcript", base=DIRECT).json() == t


def test_transcript_html_export():
    jid = S["j"]
    for base in (BASE, DIRECT):
        r = get(f"/jobs/{jid}/transcript.html", base=base)
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/html; charset=utf-8", r.headers["content-type"]
        cd = r.headers.get("content-disposition", "")
        assert cd == f'attachment; filename="roadmap2arena-{jid[:8]}-transcript.html"', cd
    body = r.text
    assert body.startswith("<!DOCTYPE html>") and body.rstrip().endswith("</html>")
    low = body.lower()
    for bad in ("<script", "<iframe", "<img", "<link", "<a "):
        assert bad not in low, f"unescaped {bad}"
    p = Balance()
    p.feed(body)
    p.close()
    assert not p.errors and p.stack == [], (p.errors[:5], p.stack)
    assert set(p.tags) <= {"html", "head", "meta", "title", "style", "body", "main", "h1", "div", "span", "code",
                           "section", "h2", "pre", "footer"}, set(p.tags)
    assert set(p.attrs) <= {"lang", "charset", "name", "content", "class", "id"}, set(p.attrs)
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;co&quot; 🚀" in body
    assert "&lt;/style&gt;&lt;script&gt;alert(2)&lt;/script&gt;" in body
    assert "Step &lt;b&gt;one&lt;/b&gt; &amp; &lt;script&gt;alert(4)&lt;/script&gt;" in body
    assert "&lt;iframe src=//evil&gt;" in body  # step description inside the prompt
    text = "".join(p.text)
    pos = [text.index(f"Step {i}: ") for i in (1, 2, 3)]
    assert pos == sorted(pos)
    assert "read_item" in text and "../evil.py" in text and text.count("User") >= 3
    S["html_len"] = len(body)


# ================================================================== files
def test_files_list():
    jid = S["j"]
    r = get(f"/jobs/{jid}/files")
    assert r.status_code == 200
    files = r.json()
    assert [f["path"] for f in files] == sorted(f["path"] for f in files)
    by = {f["path"]: f for f in files}
    assert set(by) == {"README.md", "../evil.py", "/abs/x.py", "app/main.py", "app/models.py", "pyproject.toml"}, set(by)
    for f in files:
        assert set(f) == {"path", "step_index", "versions", "size", "zip_path", "zip_skip_reason"}, set(f)
    assert by["app/main.py"]["versions"] == [1, 3] and by["app/main.py"]["step_index"] == 3
    assert by["../evil.py"]["zip_path"] is None and ".." in by["../evil.py"]["zip_skip_reason"]
    assert by["/abs/x.py"]["zip_path"] == "abs/x.py" and by["/abs/x.py"]["zip_skip_reason"] is None
    assert by["pyproject.toml"]["versions"] == [1]
    S["files"] = by


def test_file_download_bytes():
    jid = S["j"]
    s1 = {a["path"]: a["content"] for a in get(f"/jobs/{jid}/steps/1").json()["artifacts"]}
    s3 = {a["path"]: a["content"] for a in get(f"/jobs/{jid}/steps/3").json()["artifacts"]}
    r = get(f"/jobs/{jid}/files/download", params={"path": "app/main.py"})
    assert r.status_code == 200 and r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.content == s3["app/main.py"].encode() and len(r.content) == S["files"]["app/main.py"]["size"]
    cd = r.headers["content-disposition"]
    assert cd.startswith('attachment; filename="main.py"'), cd
    r = get(f"/jobs/{jid}/files/download", params={"path": "app/main.py", "step": 1})
    assert r.status_code == 200 and r.content == s1["app/main.py"].encode() and b"read_item" not in r.content
    assert get(f"/jobs/{jid}/files/download", params={"path": "app/main.py", "step": 2}).status_code == 404
    for path, f in S["files"].items():
        r = get(f"/jobs/{jid}/files/download", params={"path": path})
        assert r.status_code == 200 and len(r.content) == f["size"], path
    r = get(f"/jobs/{jid}/files/download", params={"path": "../evil.py"})
    assert r.headers["content-disposition"].startswith('attachment; filename="evil.py"') and b"never be written" in r.content
    r = get(f"/jobs/{jid}/files/download", params={"path": "/abs/x.py"}, base=DIRECT)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith('attachment; filename="x.py"')


def test_file_download_traversal_and_validation():
    jid = S["j"]
    trav = ["../../etc/passwd", "/etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/etc/passwd",
            "app/../../../etc/passwd", "app/../app/main.py", "./app/main.py", "app//main.py", "app%2fmain.py",
            "....//....//etc/passwd", "..\\..\\windows\\win.ini", "app/main.py\x00.txt", "file:///etc/passwd",
            "~/.ssh/id_rsa", "/app/backend/.env", "../backend/.env", "../../../../app/backend/.env", "README.MD",
            " app/main.py", "evil.py", "abs/x.py", "x" * 5000, "😀/../../etc/passwd"]
    for p in trav:
        r = get(f"/jobs/{jid}/files/download", params={"path": p})
        assert r.status_code == 404, (p[:40], r.status_code, r.text[:80])
        assert b"root:" not in r.content and b"MONGO_URL" not in r.content
        assert r.headers["content-type"].startswith("application/json")
    # raw (pre-encoded) query strings and encoded slashes in the URL path
    for raw in ("path=%2e%2e%2f%2e%2e%2fetc%2fpasswd", "path=%2Fetc%2Fpasswd", "path=..%252f..%252fetc%252fpasswd",
                "path=app%2Fmain.py&step=99999", "path=%00"):
        r = c.get(f"{BASE}/jobs/{jid}/files/download?{raw}")
        assert r.status_code == 404, (raw, r.status_code)
    r = c.get(f"{BASE}/jobs/{jid}/files/download?path=app%2Fmain.py")
    assert r.status_code == 200  # %2F in the query is just "/"
    # %2F in the URL path is decoded to "/" by the server (Starlette), so this is just the normal route
    r = c.get(f"{BASE}/jobs/{jid}/files%2Fdownload?path=app/main.py")
    assert r.status_code == 200 and b"read_item" in r.content
    for url in (f"{BASE}/jobs/{jid}/files/..%2F..%2F..%2Fetc%2Fpasswd",
                f"{BASE}/jobs/{jid}%2F..%2F..%2Fconfig/files", f"{BASE}/jobs/..%2F{jid}/files"):
        r = c.get(url)
        assert r.status_code in (404, 405), (url, r.status_code)
        assert b"root:" not in r.content and b"MONGO_URL" not in r.content
    for params in ({}, {"path": ""}, {"path": "app/main.py", "step": 0}, {"path": "app/main.py", "step": 100001},
                   {"path": "app/main.py", "step": -1}, {"path": "app/main.py", "step": "abc"},
                   {"path": "app/main.py", "step": 1.5}, {"path": "app/main.py", "step": 2 ** 70}, {"step": 1}):
        r = get(f"/jobs/{jid}/files/download", params=params)
        assert r.status_code == 422, (params, r.status_code, r.text[:80])
    assert get(f"/jobs/{jid}/files/download", params={"path": "app/main.py", "step": 100000}).status_code == 404


def test_unknown_ids_and_methods():
    for jid in (str(uuid.uuid4()), "not-a-uuid", "x" * 300, "507f1f77bcf86cd799439011", "%F0%9F%98%80"):
        for sub in ("transcript", "transcript.html", "files", "clone-source"):
            r = get(f"/jobs/{jid}/{sub}")
            assert r.status_code == 404, (jid[:20], sub, r.status_code)
            assert "detail" in r.json()
        assert get(f"/jobs/{jid}/files/download", params={"path": "app/main.py"}).status_code == 404
    jid = S["j"]
    for sub in ("transcript", "transcript.html", "files", "files/download?path=app/main.py", "clone-source"):
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            r = c.request(m, f"{BASE}/jobs/{jid}/{sub}")
            assert r.status_code == 405, (m, sub, r.status_code)
    assert get(f"/jobs/{jid}/transcript.json").status_code == 404
    assert get(f"/jobs/{jid}/files/nope").status_code == 404


# ================================================================== non-done jobs + clone
def test_running_queued_error_jobs():
    blocker = create_ok("stub-slow", 1, "blk")
    q = create_ok("gpt-4o", 2, "queued")
    assert job(q)["status"] == "queued"
    t = get(f"/jobs/{q}/transcript").json()
    assert t["turns"] == [] and t["status"] == "queued" and t["finished_at"] is None
    h = get(f"/jobs/{q}/transcript.html")
    assert h.status_code == 200 and "No steps were sent yet." in h.text
    assert get(f"/jobs/{q}/files").json() == []
    assert get(f"/jobs/{q}/files/download", params={"path": "app/main.py"}).status_code == 404
    cs = get(f"/jobs/{q}/clone-source").json()
    assert cs["source_job_id"] == q and cs["step_total"] == 2
    # running job: one running turn, no response yet
    end = time.time() + 5
    while time.time() < end and get(f"/jobs/{blocker}/transcript").json()["turns"] == []:
        time.sleep(0.2)
    t = get(f"/jobs/{blocker}/transcript").json()
    assert [x["status"] for x in t["turns"]] == ["running"] and t["turns"][0]["response"] == ""
    assert "(waiting for response)" in get(f"/jobs/{blocker}/transcript.html").text
    assert get(f"/jobs/{blocker}/files").json() == []
    # clone while busy -> queued, cloned_from visible in GET /queue
    src = get(f"/jobs/{S['j']}/clone-source").json()
    r = create({"arena_url": src["arena_url"], "model": "gpt-4o", "project_context": src["project_context"] + " (edited)",
                "roadmap_md": src["roadmap_md"].replace("Third step", "Third step edited"), "cloned_from": S["j"]})
    assert r.status_code == 201 and r.json()["status"] == "queued", r.text
    clone = r.json()["job_id"]
    S["clone"] = clone
    qq = get("/queue").json()
    assert [x["cloned_from"] for x in qq["queued"] if x["job_id"] == clone] == [S["j"]]
    assert c.delete(f"{BASE}/queue/{q}").status_code == 200
    t = get(f"/jobs/{q}/transcript").json()
    assert t["status"] == "cancelled" and t["turns"] == []
    for jid in (blocker, clone):
        wait(jid, 30)
    # error job
    e = create_ok("stub-503", 2, "err")
    wait(e)
    t = get(f"/jobs/{e}/transcript").json()
    assert len(t["turns"]) == 1 and t["turns"][0]["status"] == "error" and "503" in t["turns"][0]["error"]
    assert "arena2api returned 503" in get(f"/jobs/{e}/transcript.html").text
    assert get(f"/jobs/{e}/files").json() == []


def test_clone_source_and_cloned_from():
    src_id, clone = S["j"], S["clone"]
    src = get(f"/jobs/{src_id}/clone-source")
    assert src.status_code == 200
    cs = src.json()
    assert set(cs) == {"source_job_id", "title", "arena_url", "model", "project_context", "roadmap_md", "step_total"}
    j = job(src_id)
    assert cs == {"source_job_id": src_id, "title": j["title"], "arena_url": j["arena_url"], "model": j["model"],
                  "project_context": j["project_context"], "roadmap_md": j["roadmap_md"], "step_total": 3}
    cj = job(clone)
    assert cj["cloned_from"] == src_id and cj["status"] == "done" and cj["restarted_from"] is None
    assert cj["project_context"] == XSS_CTX + " (edited)" and cj["steps"][2]["title"] == "Third step edited"
    assert any(e["msg"] == f"Clone of job {src_id}" for e in cj["log"])
    lst = get("/jobs", params={"limit": 200}).json()
    assert [x["cloned_from"] for x in lst if x["job_id"] == clone] == [src_id]
    assert [x["cloned_from"] for x in lst if x["job_id"] == src_id] == [None]
    assert job(src_id)["cloned_from"] is None
    # clone of a clone
    r = create({"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "cc"), "cloned_from": clone})
    assert r.status_code == 201
    cc = r.json()["job_id"]
    wait(cc)
    assert job(cc)["cloned_from"] == clone
    # direct port agrees
    assert get(f"/jobs/{cc}", base=DIRECT).json()["cloned_from"] == clone
    # bad cloned_from -> 422, nothing created
    before = len(get("/jobs", params={"limit": 200}).json())
    for bad in (str(uuid.uuid4()), "does-not-exist", "", "x" * 300, 123, ["a"], {"id": src_id}, True):
        r = create({"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "bad"), "cloned_from": bad})
        assert r.status_code == 422, (bad, r.status_code, r.text[:100])
        assert "detail" in r.json()
    r = create({"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "bad2"), "cloned_from": str(uuid.uuid4())})
    assert "cloned_from" in r.json()["detail"]
    assert len(get("/jobs", params={"limit": 200}).json()) == before
    # explicit null is the same as no clone
    r = create({"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "nullclone"), "cloned_from": None})
    assert r.status_code == 201
    wait(r.json()["job_id"])
    assert job(r.json()["job_id"])["cloned_from"] is None


# ================================================================== cleanup
def test_zz_cleanup():
    for jid in CREATED:
        if c.get(f"{BASE}/jobs/{jid}").json().get("status") in ("queued", "paused"):
            c.delete(f"{BASE}/queue/{jid}")
    for jid in CREATED:
        if c.get(f"{BASE}/jobs/{jid}").json().get("status") == "running":
            c.post(f"{BASE}/jobs/{jid}/stop")
    for jid in CREATED:
        wait(jid, 30)
    assert c.post(f"{BASE}/settings/reset").status_code == 200
    from pymongo import MongoClient
    env = dict(l.strip().split("=", 1) for l in open("/app/backend/.env") if "=" in l and not l.startswith("#"))
    db = MongoClient(env["MONGO_URL"], serverSelectionTimeoutMS=5000)[env["DB_NAME"]]
    ids = set(CREATED) | {j["id"] for j in db.jobs.find({"$or": [{"restarted_from": {"$in": CREATED}},
                                                                  {"cloned_from": {"$in": CREATED}}]}, {"_id": 0, "id": 1})}
    db.steps.delete_many({"job_id": {"$in": list(ids)}})
    db.jobs.delete_many({"id": {"$in": list(ids)}})
    q = get("/queue").json()
    assert q["running"] is None and q["count"] == 0, q


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
