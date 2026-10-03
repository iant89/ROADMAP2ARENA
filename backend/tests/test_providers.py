"""Providers (feat/providers): key encryption, never leaking the key, migration, CRUD, model fetch,
error mapping and jobs with a provider. contracts.md "Providers".

Runs only against local stand-ins: a private backend per scenario (isolated_server.py, throwaway
DB, temp data dir) and tests/provider_stub.py on a free port. No real provider is contacted.

    ./venv/bin/python backend/tests/test_providers.py [test names]     (from the repository root)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

import httpx
from cryptography.fernet import Fernet

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)
for k, v in {"MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "unused", "ARENA2API_URL": "http://127.0.0.1:9",
             "ARENA2API_MODEL": "gpt-4o", "ARENA_STEP_DELAY_SECONDS": "0", "ARENA_REQUEST_TIMEOUT_SECONDS": "30",
             "CORS_ORIGINS": "*"}.items():
    os.environ.setdefault(k, v)

from isolated_server import IsolatedServer, free_port  # noqa: E402
from provider_stub import GOOD_KEY  # noqa: E402
import providers  # noqa: E402  (pure helpers only - unit tests below)

PY = sys.executable
STUB_PORT = free_port()
STUB = f"http://127.0.0.1:{STUB_PORT}"
ARENA_STUB = os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090")
c = httpx.Client(timeout=30)
ROADMAP = "# Prov test\n\n### Step one\nMake a file.\n\n### Step two\nAnother file.\n"
SECRET = Fernet.generate_key().decode()
TITLE = "R2A test title"


def base(mode: str) -> str:
    return f"{STUB}/{mode}/v1"


def env(**extra) -> dict:
    return {"R2A_SECRET_KEY": SECRET, "ARENA2API_URL": ARENA_STUB, "R2A_DATA_DIR": tempfile.mkdtemp(prefix="r2a-prov-"),
            "ARENA_REQUEST_TIMEOUT_SECONDS": "10", **extra}


def wait(B: str, jid: str, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"{B}/jobs/{jid}").json()
        if j["status"] in ("done", "error", "stopped", "cancelled"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {jid} did not finish: {j['status']}")


def create_provider(B: str, **body) -> dict:
    r = c.post(f"{B}/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def run_job(B: str, **body) -> dict:
    r = c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, **body})
    assert r.status_code == 201, r.text
    return wait(B, r.json()["job_id"])


def stub_requests() -> list[dict]:
    return c.get(f"{STUB}/_requests").json()


def everything_text(B: str, srv: IsolatedServer) -> str:
    """Every API surface + server log + Mongo docs (except the encrypted field) as one string."""
    parts = [c.get(f"{B}/providers").text, c.get(f"{B}/jobs?limit=200").text, c.get(f"{B}/queue").text,
             c.get(f"{B}/settings").text, c.get(f"{B}/config").text]
    for j in c.get(f"{B}/jobs?limit=200").json():
        jid = j["job_id"]
        parts += [c.get(f"{B}/jobs/{jid}").text, c.get(f"{B}/jobs/{jid}/transcript").text,
                  c.get(f"{B}/jobs/{jid}/transcript.html").text, c.get(f"{B}/jobs/{jid}/clone-source").text]
        for i in range(1, j["step_total"] + 1):
            parts.append(c.get(f"{B}/jobs/{jid}/steps/{i}").text)
    db = srv.db()
    for coll in ("jobs", "steps", "settings", "notifications"):
        parts += [json.dumps(d, default=str) for d in db[coll].find({}, {"_id": 0})]
    parts += [json.dumps({k: v for k, v in d.items() if k != "api_key_enc"}, default=str)
              for d in db.providers.find({}, {"_id": 0})]
    parts.append(open(srv.log_path).read())
    return "\n".join(parts)


# ================================================================== unit (no server)
def test_unit_redact_and_mapping():
    key = "sk-live-abcdefghijklmnop"
    assert providers.redact(f"bad key {key}!", [key, None, ""]) == "bad key [redacted]!"
    assert providers.redact("abc", ["ab"]) == "abc"  # too short to redact safely, never mangles text
    cases = {401: "(API key rejected)", 403: "(access denied)", 429: "(rate limit or quota exceeded)", 500: "(server error)"}
    for code, tag in cases.items():
        m = providers.status_message(code, f"echo {key}", name="OpenAI", model="gpt-x", secrets=[key])
        assert m.startswith(f"OpenAI returned {code} {tag}") and key not in m and "[redacted]" in m, m
    m = providers.status_message(404, "nope", name="Groq", model="llama-9", context="job")
    assert "model 'llama-9' not found or wrong base URL" in m, m
    m = providers.status_message(404, "nope", name="Groq", context="models")
    assert "check the base URL (it usually ends in /v1)" in m, m
    m = providers.status_message(503, "Extension not connected", name="arena2api", arena=True)
    assert m == "arena2api returned 503: Extension not connected - check that the arena2api Chrome tab is open and pushing tokens", m
    assert "Chrome tab" not in providers.status_message(503, "x", name="OpenAI", arena=False)
    long = providers.status_message(500, "x" * 1000, name="P")
    assert len(long) < 300, len(long)


def test_unit_validation_helpers():
    assert providers.normalize_base_url(" https://openrouter.ai/api/v1/ ") == "https://openrouter.ai/api/v1"
    for bad in ("ftp://x", "localhost:1234/v1", "", "http://", "https://u:p@host/v1", "http://h/v1?x=1", 5, None):
        try:
            providers.normalize_base_url(bad)
            raise AssertionError(f"accepted {bad!r}")
        except providers.ProviderError as e:
            assert e.status == 422
    assert providers.validate_headers({"HTTP-Referer": "https://x.test", "X-Title": " T "}) == {
        "HTTP-Referer": "https://x.test", "X-Title": "T"}
    for bad in ({"Authorization": "Bearer x"}, {"x-api-key": "k"}, {"Cookie": "a"}, {"bad name": "v"},
                {"X-A": "line\nbreak"}, {"X-A": 5}, {f"X-{i}": "v" for i in range(11)}, {"X-A": "1", "x-a": "2"}, ["x"]):
        try:
            providers.validate_headers(bad)
            raise AssertionError(f"accepted {bad!r}")
        except providers.ProviderError as e:
            assert e.status == 422
    assert providers.origin("https://api.openai.com/v1") == providers.origin("https://API.openai.com:443/x")
    assert providers.origin("http://localhost:11434/v1") != providers.origin("http://localhost:1234/v1")
    assert providers.legacy_base("http://h:9090/") == "http://h:9090/v1"
    assert providers.legacy_base("http://h:9090/v1") == "http://h:9090/v1"
    assert providers.parse_models({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, {"x": 1}]}) == ["a", "b"]
    assert providers.parse_models([{"id": "z"}, "y"]) == ["y", "z"]
    assert providers.parse_models({"models": [{"name": "llama3"}]}) == ["llama3"]
    try:
        providers.parse_models({"object": "error"})
        raise AssertionError("accepted a non-list")
    except ValueError:
        pass


def test_unit_key_encryption_roundtrip():
    os.environ["R2A_SECRET_KEY"] = SECRET
    token = providers.encrypt_key("sk-unit-123456")
    assert "sk-unit-123456" not in token
    assert providers.decrypt_key({"api_key_enc": token}) == ("sk-unit-123456", None)
    os.environ["R2A_SECRET_KEY"] = Fernet.generate_key().decode()
    key, err = providers.decrypt_key({"api_key_enc": token})
    assert key is None and "cannot be decrypted" in err
    os.environ["R2A_SECRET_KEY"] = ""
    try:
        providers.encrypt_key("sk-unit-123456")
        raise AssertionError("encrypted without R2A_SECRET_KEY")
    except providers.ProviderError as e:
        assert e.status == 409 and "R2A_SECRET_KEY" in e.detail
    assert providers.decrypt_key({"api_key_enc": None}) == (None, None)


# ================================================================== server scenarios
SRV_REF: dict = {}


def srv_doc(B: str, pid: str) -> dict:
    return SRV_REF[B].db().providers.find_one({"id": pid}, {"_id": 0})


class Srv(IsolatedServer):
    def __enter__(self):
        b = super().__enter__()
        SRV_REF[b] = self
        return b


def test_migration_default_provider_and_legacy_jobs():
    srv = IsolatedServer(step_delay=0, extra_env=env())
    db = srv.db()  # seed BEFORE the first start: an old installation with a custom URL and a legacy job
    db.settings.insert_one({"_id": "app", "arena_url": ARENA_STUB + "/", "model": "gpt-4o", "step_delay_seconds": 0,
                            "request_timeout_seconds": 10, "updated_at": "2026-01-01T00:00:00.000Z"})
    with srv as B:
        p = c.get(f"{B}/providers").json()
        assert len(p["providers"]) == 1, p
        mig = p["providers"][0]
        assert mig["name"] == "arena2api (local)" and mig["preset"] == "arena2api" and mig["migrated"] is True
        assert mig["base_url"] == ARENA_STUB + "/v1" and mig["is_default"] and p["default_provider_id"] == mig["id"]
        assert mig["api_key_set"] is False and p["encryption"] == "ok"
        assert {x["id"] for x in p["presets"]} == {"openai", "openrouter", "groq", "ollama", "lmstudio", "arena2api", "custom"}
        # legacy job (explicit arena_url) still works exactly as before
        legacy = run_job(B, arena_url=ARENA_STUB, model="gpt-4o")
        assert legacy["status"] == "done" and legacy["provider"] is None and legacy["arena_url"] == ARENA_STUB, legacy
        assert any("arena2api " + ARENA_STUB in e["msg"] for e in legacy["log"]), legacy["log"]
        # a job without provider/url uses the migrated default provider
        j = run_job(B, model="gpt-4o")
        assert j["status"] == "done" and j["provider"] == {"id": mig["id"], "name": "arena2api (local)",
                                                           "preset": "arena2api", "base_url": ARENA_STUB + "/v1"}, j
        assert j["arena_url"] == ARENA_STUB + "/v1"
        # legacy job resumes/restarts on the legacy path; a provider job's arena_url reused as override still works
        r = c.post(f"{B}/jobs/{legacy['job_id']}/restart", json={})
        assert r.status_code == 201 and wait(B, r.json()["job_id"])["status"] == "done"
        r = c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, "arena_url": j["arena_url"], "model": "gpt-4o"})
        assert wait(B, r.json()["job_id"])["status"] == "done"
        # 503 from arena2api keeps the old wording and hint
        e = run_job(B, model="stub-503")
        assert "arena2api (local) returned 503" in e["error"] and "Chrome tab is open" in e["error"], e["error"]
        # idempotent: deleting every provider and restarting does not recreate it
        assert c.delete(f"{B}/providers/{mig['id']}").status_code == 204
        assert c.get(f"{B}/providers").json()["default_provider_id"] is None
        # with no default provider, a job without url falls back to the legacy settings URL
        f = run_job(B, model="gpt-4o")
        assert f["status"] == "done" and f["provider"] is None and f["arena_url"].rstrip("/") == ARENA_STUB, f
    srv2 = IsolatedServer(step_delay=0, extra_env=env())
    srv2.db_name = srv.db_name  # same (already dropped) name: fresh DB but with the flag set first
    srv2.db().settings.insert_one({"_id": "app", "arena_url": ARENA_STUB, "model": "gpt-4o", "step_delay_seconds": 0,
                                   "request_timeout_seconds": 10, "providers_migrated": True})
    with srv2 as B:
        assert c.get(f"{B}/providers").json()["providers"] == []


def test_crud_validation_and_defaults():
    with Srv(step_delay=0, extra_env=env()) as B:
        mig = c.get(f"{B}/providers").json()["providers"][0]
        p = create_provider(B, name="OpenRouter test", preset="openrouter", base_url=base("auth") + "/",
                            api_key=GOOD_KEY, headers={"HTTP-Referer": "https://r2a.test", "X-Title": TITLE},
                            default_model="stub-b")
        assert p["base_url"] == base("auth") and p["api_key_set"] and p["api_key_error"] is None
        assert p["headers"] == {"HTTP-Referer": "https://r2a.test", "X-Title": TITLE} and not p["is_default"]
        assert "api_key" not in p and "api_key_enc" not in p and GOOD_KEY not in json.dumps(p)
        # validation
        for body, code in (({"name": "", "base_url": base("ok")}, 422), ({"name": "x", "base_url": "ftp://x"}, 422),
                           ({"name": "x", "base_url": base("ok"), "preset": "nope"}, 422),
                           ({"name": "x", "base_url": base("ok"), "headers": {"Authorization": "Bearer x"}}, 422),
                           ({"name": "x", "base_url": base("ok"), "api_key": ""}, 422),
                           ({"name": "x", "base_url": base("ok"), "api_key": 5}, 422),
                           ({"name": "x", "base_url": base("ok"), "evil": 1}, 422),
                           ({"name": "openrouter TEST", "base_url": base("ok")}, 409)):
            r = c.post(f"{B}/providers", json=body)
            assert r.status_code == code, (body, r.status_code, r.text)
        bad_key = "sk-\u00e9-nonascii-secret-value"
        r = c.post(f"{B}/providers", json={"name": "x", "base_url": base("ok"), "api_key": bad_key})
        assert r.status_code == 422 and bad_key not in r.text and "nonascii-secret" not in r.text
        r = c.post(f"{B}/providers", content=b'{"name": "x", "api_key": "sk-leak-me-0000"', headers={"content-type": "application/json"})
        assert r.status_code == 422 and "sk-leak-me" not in r.text
        # update: rename, headers, default model; key unchanged when omitted
        r = c.put(f"{B}/providers/{p['id']}", json={"name": "OR", "default_model": "stub-c", "headers": {}})
        assert r.status_code == 200 and r.json()["name"] == "OR" and r.json()["api_key_set"] and r.json()["headers"] == {}
        assert c.put(f"{B}/providers/{p['id']}", json={"bogus": 1}).status_code == 422
        assert c.put(f"{B}/providers/nope", json={"name": "z"}).status_code == 404
        # host change with a stored key requires re-entering/clearing it
        r = c.put(f"{B}/providers/{p['id']}", json={"base_url": "http://127.0.0.1:9/v1"})
        assert r.status_code == 422 and "Re-enter the API key" in r.json()["detail"], r.text
        assert c.put(f"{B}/providers/{p['id']}", json={"base_url": base("auth") + "x"}).status_code == 200  # same origin ok
        assert c.put(f"{B}/providers/{p['id']}", json={"base_url": base("auth")}).status_code == 200
        r = c.put(f"{B}/providers/{p['id']}", json={"api_key": GOOD_KEY, "clear_api_key": True})
        assert r.status_code == 422
        # default
        r = c.post(f"{B}/providers/{p['id']}/default")
        assert r.status_code == 200 and r.json()["is_default"]
        lst = c.get(f"{B}/providers").json()
        assert lst["default_provider_id"] == p["id"] and [x["name"] for x in lst["providers"]] == ["arena2api (local)", "OR"]
        assert c.post(f"{B}/providers/nope/default").status_code == 404
        # make_default on create
        q = create_provider(B, name="Local", preset="ollama", base_url=base("ok"), make_default=True)
        assert c.get(f"{B}/providers").json()["default_provider_id"] == q["id"]
        # clear key
        r = c.put(f"{B}/providers/{p['id']}", json={"clear_api_key": True})
        assert r.status_code == 200 and r.json()["api_key_set"] is False
        assert srv_doc(B, p["id"])["api_key_enc"] is None
        # delete default -> default becomes null; 404 afterwards
        assert c.delete(f"{B}/providers/{q['id']}").status_code == 204
        assert c.get(f"{B}/providers").json()["default_provider_id"] is None
        assert c.delete(f"{B}/providers/{q['id']}").status_code == 404
        assert c.get(f"{B}/providers").json()["providers"][0]["id"] == mig["id"]


def test_key_encrypted_sent_and_never_leaked():
    srv = Srv(step_delay=0, extra_env=env())
    with srv as B:
        c.post(f"{STUB}/_reset")
        p = create_provider(B, name="Keyed", preset="custom", base_url=base("auth"), api_key=GOOD_KEY,
                            headers={"X-Title": TITLE}, default_model="stub-a")
        doc = srv.db().providers.find_one({"id": p["id"]})
        assert doc["api_key_enc"] and GOOD_KEY not in json.dumps(doc, default=str)
        assert Fernet(SECRET.encode()).decrypt(doc["api_key_enc"].encode()).decode() == GOOD_KEY
        # the key and headers are actually sent (models + chat)
        m = c.get(f"{B}/providers/{p['id']}/models")
        assert m.status_code == 200 and m.json() == {"models": ["stub-a", "stub-b", "stub-c"], "count": 3}, m.text
        j = run_job(B, provider_id=p["id"])
        assert j["status"] == "done" and j["model"] == "stub-a", j
        sent = [r for r in stub_requests() if r["path"].startswith("/auth/")]
        assert sent and all(r["headers"].get("authorization") == f"Bearer {GOOD_KEY}" for r in sent), sent
        assert all(r["headers"].get("x-title") == TITLE for r in sent)
        assert any(r["path"] == "/auth/v1/chat/completions" for r in sent)
        # a provider that echoes the key in its error: redacted in job error, step, log, models, test
        e = create_provider(B, name="Echo", base_url=base("echo"), api_key=GOOD_KEY, headers={"X-Title": TITLE})
        ej = run_job(B, provider_id=e["id"], model="m")
        assert ej["status"] == "error" and "Echo returned 401 (API key rejected)" in ej["error"], ej["error"]
        assert "[redacted]" in ej["error"] and GOOD_KEY not in ej["error"] and TITLE not in ej["error"]
        r = c.get(f"{B}/providers/{e['id']}/models")
        assert r.status_code == 502 and GOOD_KEY not in r.text and "[redacted]" in r.text, r.text
        r = c.post(f"{B}/providers/test", json={"base_url": base("echo"), "api_key": GOOD_KEY, "headers": {"X-Title": TITLE}})
        assert r.status_code == 200 and r.json()["ok"] is False and GOOD_KEY not in r.text, r.text
        # job snapshot never holds the key; nothing anywhere contains it
        assert j["provider"] == {"id": p["id"], "name": "Keyed", "preset": "custom", "base_url": base("auth")}
        blob = everything_text(B, srv)
        assert GOOD_KEY not in blob and GOOD_KEY[-10:] not in blob, "API key leaked"
        assert "api_key_enc" not in c.get(f"{B}/providers").text


def test_model_fetch_and_test_connection():
    with IsolatedServer(step_delay=0, extra_env=env()) as B:
        ok = create_provider(B, name="OK", preset="lmstudio", base_url=base("ok"))
        assert c.get(f"{B}/providers/{ok['id']}/models").json()["models"] == ["stub-a", "stub-b", "stub-c"]
        bare = create_provider(B, name="Bare", base_url=base("bare"))
        assert c.get(f"{B}/providers/{bare['id']}/models").json()["count"] == 3
        for mode, needle in (("badjson", "unexpected /models response"), ("notfound", "check the base URL"),
                             ("auth", "(API key rejected)"), ("forbidden", "(access denied)"),
                             ("ratelimit", "(rate limit or quota exceeded)"),
                             ("down", "returned 500 (server error)")):
            p = create_provider(B, name=f"P-{mode}", base_url=base(mode))
            r = c.get(f"{B}/providers/{p['id']}/models")
            assert r.status_code == 502 and needle in r.json()["detail"], (mode, r.text)
            t = c.post(f"{B}/providers/test", json={"base_url": base(mode)}).json()
            assert t["ok"] is False and needle in t["message"], (mode, t)
            if mode == "ratelimit":
                assert t["message"].endswith("- wait and try again"), t
        dead = create_provider(B, name="Dead", base_url="http://127.0.0.1:9/v1")
        r = c.get(f"{B}/providers/{dead['id']}/models")
        assert r.status_code == 502 and "could not reach Dead at http://127.0.0.1:9/v1" in r.json()["detail"], r.text
        assert c.get(f"{B}/providers/nope/models").status_code == 404
        # draft test: ok, with a key, reuse of the stored key only for the same host
        t = c.post(f"{B}/providers/test", json={"base_url": base("ok"), "name": "Draft"}).json()
        assert t["ok"] and t["model_count"] == 3 and t["models"] == ["stub-a", "stub-b", "stub-c"] and t["latency_ms"] >= 0
        assert c.post(f"{B}/providers/test", json={"base_url": base("auth"), "api_key": GOOD_KEY}).json()["ok"]
        keyed = create_provider(B, name="Keyed", base_url=base("auth"), api_key=GOOD_KEY)
        t = c.post(f"{B}/providers/test", json={"base_url": base("auth"), "provider_id": keyed["id"]}).json()
        assert t["ok"], t
        r = c.post(f"{B}/providers/test", json={"base_url": "http://127.0.0.1:9/v1", "provider_id": keyed["id"]})
        assert r.status_code == 422 and "Enter the API key" in r.json()["detail"]
        assert c.post(f"{B}/providers/test", json={"base_url": "nope"}).status_code == 422
        assert c.post(f"{B}/providers/test", json={"base_url": base("ok"), "x": 1}).status_code == 422


def test_job_error_mapping():
    with IsolatedServer(step_delay=0, extra_env=env()) as B:
        expect = {"auth": "returned 401 (API key rejected): Incorrect API key provided - check the API key in Settings > Providers",
                  "forbidden": "returned 403 (access denied): Your account has no access to this model - the key may lack access",
                  "missingmodel": "returned 404: The model `nope-model` does not exist - model 'nope-model' not found or wrong base URL",
                  "ratelimit": "returned 429 (rate limit or quota exceeded): Rate limit reached for requests - wait, then resume the job",
                  "down": "returned 500 (server error): Internal server error",
                  "busy": "returned 503 (server error): Service unavailable"}
        for mode, needle in expect.items():
            p = create_provider(B, name=f"M-{mode}", base_url=base(mode))
            j = run_job(B, provider_id=p["id"], model="nope-model")
            assert j["status"] == "error" and j["failed_step"] == 1 and f"M-{mode} {needle}" in j["error"], (mode, j["error"])
            assert j["steps"][0]["error"] and j["steps"][1]["status"] == "pending"
        dead = create_provider(B, name="Dead", base_url="http://127.0.0.1:9/v1")
        j = run_job(B, provider_id=dead["id"], model="x")
        assert "could not reach Dead at http://127.0.0.1:9/v1 - is it running?" in j["error"], j["error"]
        arena = create_provider(B, name="Gateway", preset="arena2api", base_url=base("busy"))
        j = run_job(B, provider_id=arena["id"], model="x")
        assert "Gateway returned 503: Service unavailable - check that the arena2api Chrome tab" in j["error"], j["error"]
        # resume with a provider switch fixes the failed job and logs it
        good = create_provider(B, name="Good", base_url=base("ok"))
        failed = run_job(B, provider_id=dead["id"], model="x")
        r = c.post(f"{B}/jobs/{failed['job_id']}/resume", json={"provider_id": good["id"]})
        assert r.status_code == 200, r.text
        j = wait(B, failed["job_id"])
        assert j["status"] == "done" and j["provider"]["name"] == "Good" and j["arena_url"] == base("ok"), j
        assert any("provider Dead -> Good" in e["msg"] for e in j["log"]), [e["msg"] for e in j["log"]]
        assert c.post(f"{B}/jobs/{failed['job_id']}/restart", json={"provider_id": "nope"}).status_code == 422


def test_jobs_with_provider_snapshot_clone_queue_delete():
    srv = Srv(step_delay=0, extra_env=env(STUB_SLOW_SECONDS="3"))
    with srv as B:
        p = create_provider(B, name="Snap", preset="groq", base_url=base("ok"), default_model="stub-c")
        j = run_job(B, provider_id=p["id"], project_context="ctx")
        assert j["status"] == "done" and j["model"] == "stub-c" and j["steps_done"] == 2
        assert j["provider"] == {"id": p["id"], "name": "Snap", "preset": "groq", "base_url": base("ok")}
        assert any(f"provider Snap ({base('ok')})" in e["msg"] for e in j["log"])
        assert any('sent to Snap' in e["msg"] for e in j["log"])
        step = c.get(f"{B}/jobs/{j['job_id']}/steps/2").json()
        assert "provider stub turn 2" in step["response"]  # history carried over
        item = next(x for x in c.get(f"{B}/jobs").json() if x["job_id"] == j["job_id"])
        assert item["provider"]["name"] == "Snap"
        t = c.get(f"{B}/jobs/{j['job_id']}/transcript").json()
        assert t["provider"]["name"] == "Snap" and "Snap" in c.get(f"{B}/jobs/{j['job_id']}/transcript.html").text
        cs = c.get(f"{B}/jobs/{j['job_id']}/clone-source").json()
        assert cs["provider"] == j["provider"] and cs["model"] == "stub-c"
        # clone-and-edit submits provider_id + model
        r = c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, "provider_id": cs["provider"]["id"], "model": "stub-a",
                                      "cloned_from": j["job_id"]})
        k = wait(B, r.json()["job_id"])
        assert k["provider"]["id"] == p["id"] and k["model"] == "stub-a" and k["cloned_from"] == j["job_id"]
        assert c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, "provider_id": "nope"}).status_code == 422
        # queue shows the provider; delete is refused while a job uses it
        slow = create_provider(B, name="Slow", base_url=base("slow"))
        a = c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, "provider_id": slow["id"], "model": "m"}).json()["job_id"]
        b = c.post(f"{B}/jobs", json={"roadmap_md": ROADMAP, "provider_id": slow["id"], "model": "m"}).json()["job_id"]
        q = c.get(f"{B}/queue").json()
        assert q["running"]["provider"]["name"] == "Slow" and q["queued"][0]["provider"]["name"] == "Slow", q
        r = c.delete(f"{B}/providers/{slow['id']}")
        assert r.status_code == 409 and "use this provider" in r.json()["detail"]
        wait(B, a, 30), wait(B, b, 30)
        # renamed/moved provider: next run refreshes the snapshot
        c.put(f"{B}/providers/{p['id']}", json={"name": "Snap2", "base_url": base("bare")})
        r = c.post(f"{B}/jobs/{j['job_id']}/restart", json={})
        n = wait(B, r.json()["job_id"])
        assert n["status"] == "done" and n["provider"]["name"] == "Snap2" and n["provider"]["base_url"] == base("bare"), n
        assert any("Provider changed since the job was created" in e["msg"] for e in n["log"])
        # deleted provider: finished jobs keep their snapshot, a resume/restart fails clearly
        assert c.delete(f"{B}/providers/{p['id']}").status_code == 204
        assert c.get(f"{B}/jobs/{j['job_id']}").json()["provider"]["name"] == "Snap"
        r = c.post(f"{B}/jobs/{j['job_id']}/restart", json={})
        d = wait(B, r.json()["job_id"])
        assert d["status"] == "error" and "Provider 'Snap' was deleted" in d["error"], d["error"]


def test_no_secret_key_and_changed_secret_key():
    with IsolatedServer(step_delay=0, extra_env=env(R2A_SECRET_KEY="")) as B:
        assert c.get(f"{B}/providers").json()["encryption"] == "missing"
        r = c.post(f"{B}/providers", json={"name": "K", "base_url": base("auth"), "api_key": GOOD_KEY})
        assert r.status_code == 409 and "R2A_SECRET_KEY" in r.json()["detail"] and GOOD_KEY not in r.text
        p = create_provider(B, name="NoKey", base_url=base("ok"))  # keyless providers work without the secret
        assert run_job(B, provider_id=p["id"], model="m")["status"] == "done"
    # a key saved under one R2A_SECRET_KEY, read under another
    first = Srv(step_delay=0, extra_env=env())
    first.__enter__()
    B = first.base
    p = create_provider(B, name="Rotated", base_url=base("auth"), api_key=GOOD_KEY)
    db_name = first.db_name
    first.proc.terminate(); first.proc.wait(10)
    second = IsolatedServer(step_delay=0, extra_env=env(R2A_SECRET_KEY=Fernet.generate_key().decode()))
    second.db_name = db_name
    with second as B2:
        got = next(x for x in c.get(f"{B2}/providers").json()["providers"] if x["id"] == p["id"])
        assert got["api_key_set"] and "cannot be decrypted" in got["api_key_error"], got
        j = run_job(B2, provider_id=p["id"], model="m")
        assert j["status"] == "error" and "Provider 'Rotated'" in j["error"] and "re-enter it" in j["error"], j["error"]
        r = c.get(f"{B2}/providers/{p['id']}/models")
        assert r.status_code == 409 and "cannot be decrypted" in r.json()["detail"]
        # re-entering the key fixes it
        assert c.put(f"{B2}/providers/{p['id']}", json={"api_key": GOOD_KEY}).json()["api_key_error"] is None
        assert run_job(B2, provider_id=p["id"], model="m")["status"] == "done"
    first._log.close()


if __name__ == "__main__":
    stub = subprocess.Popen([PY, os.path.join(HERE, "provider_stub.py"), "--port", str(STUB_PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env={**os.environ, "STUB_SLOW_SECONDS": "3"})
    code = 1
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{STUB}/_requests", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        names = [n for n in list(globals()) if n.startswith("test_")]
        only = sys.argv[1:]
        passed, failed = 0, []
        for n in names:
            if only and n not in only:
                continue
            t = time.time()
            try:
                globals()[n]()
                passed += 1
                print(f"PASS {n} ({time.time() - t:.1f}s)", flush=True)
            except Exception as e:  # noqa: BLE001
                failed.append(n)
                print(f"FAIL {n}: {type(e).__name__}: {e}", flush=True)
                traceback.print_exc(limit=3)
        print(f"\n{passed} passed, {len(failed)} failed: {failed}")
        code = 1 if failed else 0
    finally:
        stub.terminate()
        stub.wait(10)
    sys.exit(code)
