"""Backend API tests for ROADMAP2ARENA.

pytest-style (plain asserts, test_* functions, run in file order). pytest and
requests are not installed on this box, so this uses httpx (in /app/venv) and
can be run with the bundled runner:  /app/venv/bin/python /app/backend/tests/run_tests.py
Base URL: env TEST_BASE_URL (default http://127.0.0.1:8001). Only the local arena
stand-in at http://127.0.0.1:9090 is used. Every created job is deleted from Mongo
at the end (only ids this suite created).
"""
from __future__ import annotations

import io
import os
import re
import secrets
import threading
import time
import uuid
import zipfile

import httpx

BASE = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8001").rstrip("/") + "/api"
PROXY = "http://localhost:8080/api"
STUB = "http://127.0.0.1:9090"
SUFFIX = secrets.token_hex(3)
CREATED: list[str] = []
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
c = httpx.Client(timeout=20)

ROADMAP3 = f"""# test_{SUFFIX} Roadmap 🚀 Ünïcode <&>

Intro paragraph ignored.

### Setup project
Create the skeleton.

```
### not a step (inside fence)
```

### Add models
Models here.

- [x] done item ignored

### Wire router
Router.
"""


def roadmap(n: int, tag: str) -> str:
    return f"# test_{SUFFIX}_{tag}\n\n" + "\n".join(f"### Step {i}\nDo {i}.\n" for i in range(1, n + 1))


def no_id(obj, path="$"):
    if isinstance(obj, dict):
        assert "_id" not in obj, f"_id leaked at {path}"
        for k, v in obj.items():
            no_id(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            no_id(v, f"{path}[{i}]")


def is_uuid4(s):
    return uuid.UUID(s).version == 4 and str(uuid.UUID(s)) == s


def create(body, expect=201):
    r = c.post(f"{BASE}/jobs", json=body)
    if r.status_code == 201:
        CREATED.append(r.json()["job_id"])
    assert r.status_code == expect, (r.status_code, r.text)
    return r


def wait(job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"{BASE}/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def wait_all():
    for jid in CREATED:
        wait(jid)


# ---------------------------------------------------------------- basics
def test_config():
    r = c.get(f"{BASE}/config")
    assert r.status_code == 200
    d = r.json()
    # request_timeout_seconds added with runtime settings (feat/tabs-and-queue)
    assert set(d) == {"arena_url", "model", "step_delay_seconds", "request_timeout_seconds"}
    assert isinstance(d["step_delay_seconds"], (int, float))


def test_proxy_routing():
    r = httpx.get(f"{PROXY}/config", timeout=10)
    assert r.status_code == 200 and "arena_url" in r.json()


def test_unknown_route_404_and_wrong_method_405():
    assert c.get(f"{BASE}/nope").status_code == 404
    assert c.delete(f"{BASE}/jobs").status_code == 405
    assert c.put(f"{BASE}/config", json={}).status_code == 405
    assert c.get(f"{BASE}/roadmap/parse").status_code == 405


# ---------------------------------------------------------------- parse
def test_parse_valid():
    r = c.post(f"{BASE}/roadmap/parse", json={"roadmap_md": ROADMAP3})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == f"test_{SUFFIX} Roadmap 🚀 Ünïcode <&>"
    assert [s["title"] for s in d["steps"]] == ["Setup project", "Add models", "Wire router"]
    assert [s["index"] for s in d["steps"]] == [1, 2, 3]
    assert "not a step" in d["steps"][0]["description"]


def test_parse_checkbox_items_and_crlf():
    r = c.post(f"{BASE}/roadmap/parse", json={"roadmap_md": "- [ ] One\r\n- [x] Skip\r\n* [ ] Two  \r\n"})
    assert r.status_code == 200
    assert [s["title"] for s in r.json()["steps"]] == ["One", "Two"]
    assert r.json()["title"] is None


def test_parse_validation():
    for body in [{}, {"roadmap_md": None}, {"roadmap_md": 5}, {"roadmap_md": ""}, {"roadmap_md": "   \n "},
                 {"roadmap_md": "# Only a title\nprose"}, [1, 2]]:
        r = c.post(f"{BASE}/roadmap/parse", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)
    r = c.post(f"{BASE}/roadmap/parse", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_parse_long_input():
    md = "\n".join(f"### Step {i} {'x' * 200}\n{'y' * 500}" for i in range(300))
    r = c.post(f"{BASE}/roadmap/parse", json={"roadmap_md": md})
    assert r.status_code == 200 and len(r.json()["steps"]) == 300


# ---------------------------------------------------------------- job validation
def test_job_validation_422():
    good = {"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "v")}
    bad = [
        {**good, "arena_url": "ftp://x"}, {**good, "arena_url": "localhost:9090"}, {**good, "arena_url": ""},
        {**good, "arena_url": "http://"}, {**good, "model": ""}, {**good, "model": "   "},
        {**good, "roadmap_md": ""}, {**good, "roadmap_md": "  "}, {**good, "roadmap_md": "# t\nno steps"},
        {k: v for k, v in good.items() if k != "roadmap_md"}, {**good, "roadmap_md": None},
        {**good, "roadmap_md": 123}, {**good, "model": 5}, {**good, "arena_url": ["x"]},
        {**good, "project_context": {"a": 1}},
    ]
    for body in bad:
        r = c.post(f"{BASE}/jobs", json=body)
        if r.status_code == 201:
            CREATED.append(r.json()["job_id"])
            wait(r.json()["job_id"])
        assert r.status_code == 422, (body, r.status_code, r.text)
        assert "detail" in r.json()


def test_unknown_and_malformed_ids():
    for jid in [str(uuid.uuid4()), "not-a-uuid", "507f1f77bcf86cd799439011", "x" * 300, "%F0%9F%9A%80"]:
        assert c.get(f"{BASE}/jobs/{jid}").status_code == 404, jid
        assert c.get(f"{BASE}/jobs/{jid}/steps/1").status_code == 404
        assert c.get(f"{BASE}/jobs/{jid}/download").status_code == 404


# ---------------------------------------------------------------- happy path job
STATE: dict = {}


def test_happy_job_lifecycle():
    ctx = f"test_{SUFFIX} context ✨ 日本語 \"quotes\" <b>"
    r = create({"arena_url": STUB, "model": "gpt-4o", "project_context": ctx, "roadmap_md": ROADMAP3})
    jid = r.json()["job_id"]
    # queue: POST /jobs also returns status (running|queued) and queue_position
    assert set(r.json()) == {"job_id", "status", "queue_position"} and is_uuid4(jid)
    STATE["happy"] = jid
    first = c.get(f"{BASE}/jobs/{jid}").json()
    assert first["status"] in ("running", "done")
    j = wait(jid)
    no_id(j)
    keys = {"job_id", "status", "error", "failed_step", "title", "created_at", "finished_at", "arena_url", "model",
            "project_context", "roadmap_md", "step_total", "steps_done", "steps", "log",
            "stopped_step", "restarted_from",  # added with job controls (stop/restart/resume)
            "queue_position", "queued_at", "started_at"}  # added with the job queue
    assert set(j) == keys, set(j) ^ keys
    assert j["status"] == "done" and j["error"] is None and j["failed_step"] is None
    assert j["step_total"] == 3 and j["steps_done"] == 3
    assert j["project_context"] == ctx and j["roadmap_md"] == ROADMAP3
    assert j["title"] == f"test_{SUFFIX} Roadmap 🚀 Ünïcode <&>"
    assert ISO_Z.match(j["created_at"]) and ISO_Z.match(j["finished_at"])
    assert j["finished_at"] >= j["created_at"]
    for s in j["steps"]:
        assert set(s) == {"index", "title", "description", "status", "error", "artifact_paths"}
        assert s["status"] == "done"
    paths = [s["artifact_paths"] for s in j["steps"]]
    assert sorted(paths[0]) == ["app/main.py", "pyproject.toml"]
    assert "app/models.py" in paths[1]
    assert sorted(paths[2]) == ["README.md", "app/main.py"]
    for p in sum(paths, []):
        assert "uvicorn" not in p and p.strip()
    for e in j["log"]:
        assert set(e) == {"ts", "level", "msg"} and e["level"] in ("info", "ok", "warn", "error")
        assert ISO_Z.match(e["ts"])


def test_step_detail():
    jid = STATE["happy"]
    s = c.get(f"{BASE}/jobs/{jid}/steps/1").json()
    no_id(s)
    keys = {"job_id", "index", "title", "description", "status", "prompt", "response", "error", "artifacts",
            "started_at", "finished_at"}
    assert set(s) == keys, set(s) ^ keys
    assert s["job_id"] == jid and s["index"] == 1 and s["status"] == "done"
    assert f"test_{SUFFIX} context ✨ 日本語" in s["prompt"] and "CURRENT STEP 1 of 3" in s["prompt"]
    assert "python:app/main.py" in s["response"]
    assert ISO_Z.match(s["started_at"]) and ISO_Z.match(s["finished_at"])
    arts = {a["path"]: a["content"] for a in s["artifacts"]}
    assert set(arts) == {"app/main.py", "pyproject.toml"}
    assert not arts["pyproject.toml"].startswith("# filename")
    s3 = c.get(f"{BASE}/jobs/{jid}/steps/3").json()
    assert "PREVIOUSLY CREATED FILES" in s3["prompt"] and "app/models.py" in s3["prompt"]
    assert "<!--" not in {a["path"]: a["content"] for a in s3["artifacts"]}["README.md"]


def test_step_404_and_bad_index():
    jid = STATE["happy"]
    for idx in ("0", "4", "-1", "999999"):
        assert c.get(f"{BASE}/jobs/{jid}/steps/{idx}").status_code == 404, idx
    for idx in ("abc", "1.5", "%20"):
        r = c.get(f"{BASE}/jobs/{jid}/steps/{idx}")
        assert r.status_code in (404, 422), (idx, r.status_code)
    assert c.get(f"{BASE}/jobs/{jid}/steps/99999999999999999999999").status_code in (404, 422)


def test_b001_step_index_boundaries():
    jid = STATE["happy"]
    assert c.get(f"{BASE}/jobs/{jid}/steps/1").status_code == 200
    for idx in ("0", "100000", "100001", str(2**63 - 1), str(2**63), str(2**64), "99999999999999999999999",
                "-" + str(2**63 + 1)):
        r = c.get(f"{BASE}/jobs/{jid}/steps/{idx}")
        assert r.status_code == 404, (idx, r.status_code, r.text[:100])
        assert "detail" in r.json()


def test_download_zip_rules():
    jid = STATE["happy"]
    r = c.get(f"{BASE}/jobs/{jid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert f"roadmap2arena-{jid[:8]}.zip" in r.headers.get("content-disposition", "")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    assert names == ["README.md", "abs/x.py", "app/main.py", "app/models.py", "pyproject.toml"], names
    assert "read_item" in z.read("app/main.py").decode()  # step 3 version wins
    assert all(".." not in n and not n.startswith("/") for n in names)
    log = c.get(f"{BASE}/jobs/{jid}").json()["log"]
    assert any(e["level"] == "warn" and "../evil.py" in e["msg"] for e in log)
    # repeat request is stable
    r2 = c.get(f"{BASE}/jobs/{jid}/download")
    assert sorted(zipfile.ZipFile(io.BytesIO(r2.content)).namelist()) == names


def test_list_jobs():
    r = c.get(f"{BASE}/jobs")
    assert r.status_code == 200
    d = r.json()
    no_id(d)
    assert isinstance(d, list) and 1 <= len(d) <= 20
    keys = {"job_id", "status", "created_at", "step_total", "steps_done", "title", "model", "failed_step",
            "stopped_step",  # stopped_step added with job controls
            "restarted_from", "queue_position", "queued_at", "started_at", "finished_at", "paused"}  # job queue
    for j in d:
        assert set(j) == keys, set(j) ^ keys
        assert ISO_Z.match(j["created_at"])
    assert [j["created_at"] for j in d] == sorted([j["created_at"] for j in d], reverse=True)
    assert d[0]["job_id"] == STATE["happy"]


# ---------------------------------------------------------------- failures
def test_stub_503_fails_first_step():
    jid = create({"arena_url": STUB, "model": "stub-503", "roadmap_md": roadmap(3, "503")}).json()["job_id"]
    j = wait(jid)
    assert j["status"] == "error" and j["failed_step"] == 1 and j["steps_done"] == 0
    assert "check that the arena2api Chrome tab is open" in j["error"]
    assert [s["status"] for s in j["steps"]] == ["error", "pending", "pending"]
    assert "503" in j["steps"][0]["error"]
    assert ISO_Z.match(j["finished_at"])
    r = c.get(f"{BASE}/jobs/{jid}/download")
    assert r.status_code == 409 and "detail" in r.json()
    s2 = c.get(f"{BASE}/jobs/{jid}/steps/2").json()
    assert s2["status"] == "pending" and s2["started_at"] is None and s2["prompt"] == ""


def test_stub_503_at_3_fails_partway():
    jid = create({"arena_url": STUB, "model": "stub-503-at-3", "project_context": f"test_{SUFFIX}_B",
                  "roadmap_md": roadmap(4, "503at3")}).json()["job_id"]
    j = wait(jid)
    assert j["status"] == "error" and j["failed_step"] == 3 and j["steps_done"] == 2
    assert [s["status"] for s in j["steps"]] == ["done", "done", "error", "pending"]
    assert "check that the arena2api Chrome tab is open" in j["steps"][2]["error"]
    r = c.get(f"{BASE}/jobs/{jid}/download")
    assert r.status_code == 200
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == ["abs/x.py", "app/main.py", "app/models.py", "pyproject.toml"], names
    # jobs never mix: fresh conversation and own context
    s1 = c.get(f"{BASE}/jobs/{jid}/steps/1").json()
    assert f"test_{SUFFIX}_B" in s1["prompt"] and "context ✨" not in s1["prompt"]
    assert "Setting up the package" in s1["response"]
    l = c.get(f"{BASE}/jobs").json()
    assert any(x["job_id"] == jid and x["failed_step"] == 3 for x in l)


def test_unreachable_arena():
    jid = create({"arena_url": "http://127.0.0.1:9", "model": "gpt-4o", "roadmap_md": roadmap(1, "unreach")}).json()["job_id"]
    j = wait(jid)
    assert j["status"] == "error" and j["failed_step"] == 1 and "could not reach" in j["error"]


# ---------------------------------------------------------------- queue / concurrency
# Changed with the job queue: a second job is no longer rejected with 409, it is
# queued (201, status "queued", queue_position 1) and starts when the first ends.
def test_second_job_409_while_running():
    jid = create({"arena_url": STUB, "model": "stub-slow", "roadmap_md": roadmap(1, "slow")}).json()["job_id"]
    r = c.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": "gpt-4o", "roadmap_md": roadmap(1, "second")})
    if r.status_code == 201:
        CREATED.append(r.json()["job_id"])
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "queued" and r.json()["queue_position"] == 1, r.json()
    assert c.get(f"{BASE}/jobs/{jid}").json()["status"] == "running"
    j = wait(jid, 30)
    assert j["status"] == "done"
    assert wait(r.json()["job_id"], 30)["status"] == "done"


def test_concurrent_create_only_one_wins():
    results = []
    barrier = threading.Barrier(6)

    def go(i):
        cl = httpx.Client(timeout=20)
        barrier.wait()
        results.append(cl.post(f"{BASE}/jobs", json={"arena_url": STUB, "model": "stub-slow",
                                                     "roadmap_md": roadmap(1, f"race{i}")}))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok = [r for r in results if r.status_code == 201]
    for r in ok:
        CREATED.append(r.json()["job_id"])
    codes = sorted(r.status_code for r in results)
    running = [j for j in c.get(f"{BASE}/jobs").json() if j["status"] == "running"]
    wait_all()
    # queue: all six are accepted; exactly one runs, the rest get positions 1..5
    assert codes == [201] * 6, codes
    started = [r.json()["job_id"] for r in ok if r.json()["status"] == "running"]
    assert len(started) == 1, [r.json() for r in ok]
    assert sorted(r.json()["queue_position"] for r in ok if r.json()["status"] == "queued") == [1, 2, 3, 4, 5]
    assert [j["job_id"] for j in running] == started, running
    assert all(wait(r.json()["job_id"])["status"] == "done" for r in ok)


# ---------------------------------------------------------------- cleanup
def test_zz_cleanup():
    wait_all()
    from pymongo import MongoClient
    env = dict(l.strip().split("=", 1) for l in open("/app/backend/.env") if "=" in l and not l.startswith("#"))
    db = MongoClient(env["MONGO_URL"], serverSelectionTimeoutMS=5000)[env["DB_NAME"]]
    db.steps.delete_many({"job_id": {"$in": CREATED}})
    db.jobs.delete_many({"id": {"$in": CREATED}})
    for jid in CREATED:
        assert c.get(f"{BASE}/jobs/{jid}").status_code == 404
