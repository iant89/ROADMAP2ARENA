"""Pinned arena2api integration checks. No MongoDB or real Arena requests.

Run: ./venv/bin/python backend/tests/test_gateway.py
Uses the actual upstream app, real OpenAI SDK, synthetic extension data and an
in-memory Arena HTTP transport. One short-lived CLI server is tested on a free port.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
for key, value in {
    "MONGO_URL": "mongodb://127.0.0.1:27017", "DB_NAME": "roadmap2arena_gateway_tests",
    "ARENA2API_URL": "http://127.0.0.1:9090", "ARENA2API_MODEL": "gpt-4o",
    "ARENA_STEP_DELAY_SECONDS": "0.2", "ARENA_REQUEST_TIMEOUT_SECONDS": "300", "CORS_ORIGINS": "*",
}.items():
    os.environ.setdefault(key, value)

import httpx  # noqa: E402
import httpx2  # noqa: E402
from openai import AsyncOpenAI, APIStatusError  # noqa: E402

import app_settings  # noqa: E402
import orchestrator  # noqa: E402
from arena_client import ArenaClient  # noqa: E402
from artifact_extractor import extract_artifacts  # noqa: E402
from gateway import UPSTREAM_DIR, UPSTREAM_REVISION  # noqa: E402
from gateway.runtime import GatewayConfig, check_upstream, load_upstream  # noqa: E402
import gateway.runtime as runtime  # noqa: E402

DUMMY_KEY = "dummy-gateway-key-for-local-tests-only"
DUMMY_AUTH = "dummy-arena-session-for-local-tests-only"
DUMMY_COOKIE = "dummy-cookie-for-local-tests-only"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def extension_payload():
    return {
        "auth_token": DUMMY_AUTH,
        "cookies": {"arena-auth-prod-v1": DUMMY_COOKIE},
        "v3_tokens": [{"token": "dummy-v3-token-" + "x" * 30, "age_ms": 0}],
        "models": [{"publicName": "Demo Text", "id": "demo-text-id",
                    "capabilities": {"inputCapabilities": ["text"], "outputCapabilities": ["text"]}}],
    }


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r2a-gateway-config-")
        self.addCleanup(self.temp.cleanup)
        self.env_file = Path(self.temp.name, ".env")
        self.env_file.write_text("")

    def test_defaults_are_loopback_single_service(self):
        cfg = GatewayConfig.load(self.env_file, environ={})
        self.assertEqual((cfg.host, cfg.port, cfg.api_key, cfg.log_level), ("127.0.0.1", 9090, "", "info"))

    def test_optional_default_env_file(self):
        with patch.object(runtime, "DEFAULT_ENV_FILE", Path(self.temp.name, "missing.env")):
            self.assertEqual(GatewayConfig.load(environ={}).port, 9090)

    def test_env_file_process_and_cli_precedence(self):
        self.env_file.write_text('GATEWAY_PORT=9091\nGATEWAY_HOST="127.0.0.1"\nGATEWAY_API_KEY="file-dummy-key"\n')
        cfg = GatewayConfig.load(self.env_file, environ={"GATEWAY_PORT": "9092", "GATEWAY_API_KEY": DUMMY_KEY},
                                 host="0.0.0.0", port=9093)
        self.assertEqual((cfg.host, cfg.port, cfg.api_key), ("0.0.0.0", 9093, DUMMY_KEY))
        self.assertNotIn(DUMMY_KEY, repr(cfg))

    def test_invalid_ports_fail_without_echoing_values(self):
        for value in ("0", "65536", "-1", "1.5", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GatewayConfig.load(self.env_file, environ={"GATEWAY_PORT": value})
        with self.assertRaises(ValueError):
            GatewayConfig(port=True)

    def test_invalid_hosts_and_log_levels(self):
        for host in ("", "http://127.0.0.1", "has space", "127.0.0.1\n"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                GatewayConfig(host=host)
        with self.assertRaises(ValueError):
            GatewayConfig(log_level="invalid")

    def test_key_control_characters_rejected_without_secret_in_error(self):
        with self.assertRaises(ValueError) as caught:
            GatewayConfig(api_key=DUMMY_KEY + "\r\nextra")
        self.assertNotIn(DUMMY_KEY, str(caught.exception))

    def test_non_ascii_key_rejected(self):
        with self.assertRaises(ValueError) as caught:
            GatewayConfig(api_key="dummy-key-\u00e9")
        self.assertNotIn("dummy-key", str(caught.exception))

    def test_backend_rejects_invalid_key_without_logging_it(self):
        for value in (DUMMY_KEY + "\nx", "dummy-key-\u00e9"):
            env = {**os.environ, "ARENA2API_API_KEY": value}
            result = subprocess.run([str(ROOT / "venv/bin/python"), "-c", "import settings"],
                                    cwd=ROOT / "backend", env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("printable ASCII", result.stderr)
            self.assertNotIn(DUMMY_KEY, result.stdout + result.stderr)
            self.assertNotIn("dummy-key-\u00e9", result.stdout + result.stderr)

    def test_explicit_missing_env_file_fails(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            GatewayConfig.load(Path(self.temp.name, "missing.env"), environ={})

    def test_pinned_checkout_and_unmodified_source(self):
        self.assertEqual(check_upstream(), UPSTREAM_REVISION)
        result = subprocess.run(["git", "-C", str(UPSTREAM_DIR), "diff", "--exit-code", "HEAD"], capture_output=True)
        self.assertEqual(result.returncode, 0, "Upstream source must stay unmodified")

    def test_missing_submodule_has_actionable_message(self):
        with patch.object(runtime, "UPSTREAM_DIR", Path(self.temp.name, "missing")):
            with self.assertRaisesRegex(RuntimeError, "git submodule update --init"):
                check_upstream()

    def test_unexpected_revision_is_rejected(self):
        result = SimpleNamespace(returncode=0, stdout="0" * 40)
        with patch.object(runtime.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "pinned revision"):
                check_upstream()

    def test_gateway_import_restores_unrelated_environment(self):
        with patch.dict(os.environ, {"API_KEY": "unrelated-key", "PORT": "1234", "DEBUG": "1"}):
            module = load_upstream(GatewayConfig(port=9191, api_key=DUMMY_KEY))
            self.assertEqual((module.PORT, module.API_KEY), (9191, DUMMY_KEY))
            self.assertEqual((os.environ["PORT"], os.environ["API_KEY"], os.environ["DEBUG"]),
                             ("1234", "unrelated-key", "1"))
            self.assertFalse(module.store.active)

    def test_cli_check_is_redacted_and_works_outside_checkout(self):
        env = {**os.environ, "GATEWAY_API_KEY": DUMMY_KEY}
        result = subprocess.run([str(ROOT / "run-gateway.sh"), "--env-file", str(self.env_file), "--check"],
                                cwd=self.temp.name, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["revision"], UPSTREAM_REVISION)
        self.assertTrue(payload["api_key_configured"])
        self.assertNotIn(DUMMY_KEY, result.stdout + result.stderr)

    def test_launcher_modes_are_mutually_exclusive(self):
        result = subprocess.run(["bash", str(ROOT / "run.sh")], cwd=self.temp.name,
                                env={**os.environ, "GATEWAY": "1", "STUB": "1"}, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not both", result.stderr)

    def test_launcher_rejects_invalid_mode(self):
        result = subprocess.run(["bash", str(ROOT / "run.sh")],
                                env={**os.environ, "GATEWAY": "invalid", "STUB": "0"}, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 0 or 1", result.stderr)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app_settings.env, "ARENA2API_URL", "http://127.0.0.1:9090"))
        self.stack.enter_context(patch.object(app_settings.env, "ARENA2API_API_KEY", DUMMY_KEY))

    def test_key_only_applies_to_trusted_url(self):
        self.assertEqual(app_settings.gateway_api_key("http://127.0.0.1:9090"), DUMMY_KEY)
        self.assertEqual(app_settings.gateway_api_key(" http://127.0.0.1:9090/ "), DUMMY_KEY)

    def test_key_not_forwarded_to_overrides_or_url_prefixes(self):
        for url in ("http://untrusted.test:9090", "http://127.0.0.1:9091", "http://localhost:9090",
                    "https://127.0.0.1:9090", "http://127.0.0.1:9090/other", "http://127.0.0.1:9090.evil.test"):
            with self.subTest(url=url):
                self.assertIsNone(app_settings.gateway_api_key(url))

    def test_empty_key_preserves_no_auth_mode(self):
        with patch.object(app_settings.env, "ARENA2API_API_KEY", ""):
            self.assertIsNone(app_settings.gateway_api_key("http://127.0.0.1:9090"))

    def test_provider_errors_redact_key_before_storage(self):
        request = httpx.Request("POST", "http://127.0.0.1:9090/v1/chat/completions")
        for response in (
            httpx.Response(401, json={"error": {"message": f"Invalid key {DUMMY_KEY}"}}, request=request),
            httpx.Response(500, text="x" * 180 + DUMMY_KEY, request=request),
        ):
            with self.subTest(status=response.status_code):
                error = APIStatusError("provider error", response=response, body=None)
                message = orchestrator.describe_error(error, "http://127.0.0.1:9090", 20)
                self.assertNotIn(DUMMY_KEY, message)
                self.assertNotIn(DUMMY_KEY[:12], message)
                self.assertIn("[redacted gateway key]"[:10], message)
        message = orchestrator.describe_error(RuntimeError(f"Failed with key {DUMMY_KEY}"), "http://127.0.0.1:9090", 20)
        self.assertNotIn(DUMMY_KEY, message)
        self.assertIn("[redacted gateway key]", message)

    def test_redaction_is_noop_without_key(self):
        with patch.object(app_settings.env, "ARENA2API_API_KEY", ""):
            self.assertEqual(app_settings.redact_gateway_key("unchanged message"), "unchanged message")

    def test_key_never_in_public_settings_or_env_defaults(self):
        cfg = {**app_settings.env_defaults(), "updated_at": None, "api_key": DUMMY_KEY}
        payload = app_settings.public(cfg)
        self.assertNotIn(DUMMY_KEY, json.dumps(payload))
        self.assertNotIn("api_key", payload)
        self.assertNotIn("ARENA2API_API_KEY", payload["env_defaults"])


class GatewayApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = load_upstream(GatewayConfig(api_key=DUMMY_KEY))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.module.app), base_url="http://gateway.test",
                                     trust_env=False, headers={"Authorization": f"Bearer {DUMMY_KEY}"})
        self.outbound = []
        self.status = 200
        self.reply = "```python:app/main.py\nprint('from the local test stream')\n```"

        def respond(request):
            self.outbound.append(request)
            if self.status != 200:
                return httpx.Response(self.status, text="simulated Arena failure")
            stream = "\n".join([
                "ag:" + json.dumps("simulated reasoning"),
                "a0:" + json.dumps(self.reply),
                "ad:" + json.dumps({"finishReason": "stop", "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}),
            ]) + "\n"
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        # Replace only the upstream module's HTTP namespace. SDK/test clients remain real.
        fake_http = SimpleNamespace(AsyncClient=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kw))
        self.net_patch = patch.object(self.module, "httpx", fake_http)
        self.net_patch.start()

    async def asyncTearDown(self):
        self.net_patch.stop()
        await self.http.aclose()

    async def push(self):
        result = await self.http.post("/v1/extension/push", json=extension_payload())
        self.assertEqual(result.status_code, 200)
        return result

    async def test_health_is_up_but_not_arena_ready(self):
        response = await self.http.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertFalse(response.json()["extension"]["active"])
        self.assertEqual(response.json()["extension"]["v3_tokens"], 0)
        self.assertEqual(self.outbound, [])

    async def test_waiting_models_before_extension(self):
        response = await self.http.get("/v1/models")
        self.assertEqual(response.json()["data"][0]["id"], "waiting-for-extension")
        self.assertEqual(self.outbound, [])

    async def test_disconnected_completion_returns_503_without_network(self):
        response = await self.http.post("/v1/chat/completions", json={"model": "Demo Text", "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Extension not connected", response.json()["detail"])
        self.assertEqual(self.outbound, [])

    async def test_models_and_completions_require_key(self):
        for method, path in (("GET", "/v1/models"), ("POST", "/v1/chat/completions")):
            response = await self.http.request(method, path, headers={"Authorization": "Bearer wrong-dummy-key"}, json={})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(DUMMY_KEY, response.text)
        self.assertEqual(self.outbound, [])

    async def test_auth_is_optional_when_unconfigured(self):
        self.module.API_KEY = ""
        response = await self.http.get("/v1/models", headers={"Authorization": ""})
        self.assertEqual(response.status_code, 200)

    async def test_synthetic_extension_push_and_discovery(self):
        # Upstream does NOT protect these endpoints with API_KEY. Keep its listener private.
        response = await self.http.post("/v1/extension/push", json=extension_payload(), headers={"Authorization": ""})
        self.assertEqual(response.status_code, 200)
        models = (await self.http.get("/v1/models")).json()["data"]
        self.assertEqual([m["id"] for m in models], ["Demo Text"])
        status = (await self.http.get("/v1/extension/status")).json()
        self.assertTrue(status["active"])
        self.assertTrue(status["has_auth"])
        self.assertEqual(status["v3_tokens"], 1)
        self.assertNotIn(DUMMY_AUTH, json.dumps(status))
        self.assertNotIn(DUMMY_COOKIE, json.dumps(status))
        self.assertEqual(self.outbound, [])

    async def test_invalid_json_and_empty_messages(self):
        response = await self.http.post("/v1/extension/push", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        response = await self.http.post("/v1/chat/completions", json={"model": "Demo Text", "messages": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.outbound, [])

    async def test_expired_extension_and_unknown_model(self):
        await self.push()
        response = await self.http.post("/v1/chat/completions", json={"model": "Unknown Model", "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(response.status_code, 404)
        self.module.store.last_push = time.time() - 121
        response = await self.http.post("/v1/chat/completions", json={"model": "Demo Text", "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.outbound, [])

    async def test_non_stream_response_translates_local_arena_stream(self):
        await self.push()
        response = await self.http.post("/v1/chat/completions", json={"model": "demo text", "messages": [{"role": "user", "content": "Build step one"}], "stream": False})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["content"], self.reply)
        self.assertEqual(body["choices"][0]["message"]["reasoning_content"], "simulated reasoning")
        self.assertEqual(extract_artifacts(body["choices"][0]["message"]["content"]),
                         {"app/main.py": "print('from the local test stream')\n"})
        request = self.outbound[0]
        self.assertEqual(json.loads(request.content)["modelAId"], "demo-text-id")
        self.assertEqual(request.headers["Authorization"], f"Bearer {DUMMY_AUTH}")

    async def test_stream_response_is_openai_sse(self):
        await self.push()
        response = await self.http.post("/v1/chat/completions", json={"model": "Demo Text", "messages": [{"role": "user", "content": "Hi"}], "stream": True})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("chat.completion.chunk", response.text)
        self.assertIn("data: [DONE]", response.text)

    async def test_upstream_http_error_is_propagated(self):
        await self.push()
        self.status = 429
        response = await self.http.post("/v1/chat/completions", json={"model": "Demo Text", "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(response.status_code, 429)

    async def test_real_sdk_client_preserves_history_and_extractable_files(self):
        await self.push()
        sdk_http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.module.app), trust_env=False)
        real_factory = lambda **kw: AsyncOpenAI(http_client=sdk_http, **kw)
        with patch("arena_client.AsyncOpenAI", side_effect=real_factory):
            client = ArenaClient("http://gateway.test", "Demo Text", 20, api_key=DUMMY_KEY)
        try:
            first = await client.complete("Step one")
            self.assertEqual(first, self.reply)
            self.reply = "```python:app/main.py\nprint('step two')\n```"
            second = await client.complete("Step two")
            self.assertEqual(extract_artifacts(second), {"app/main.py": "print('step two')\n"})
            self.assertEqual([m["role"] for m in client.history], ["user", "assistant", "user", "assistant"])
            prompt = json.loads(self.outbound[-1].content)["userMessage"]["content"]
            self.assertIn("<|user|>\nStep one", prompt)
            self.assertIn("<|assistant|>\n", prompt)
            self.assertIn("<|user|>\nStep two", prompt)
        finally:
            await client.close()

    async def test_real_sdk_receives_disconnect_as_503(self):
        sdk_http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.module.app), trust_env=False)
        with patch("arena_client.AsyncOpenAI", side_effect=lambda **kw: AsyncOpenAI(http_client=sdk_http, **kw)):
            client = ArenaClient("http://gateway.test", "Demo Text", 20, api_key=DUMMY_KEY)
        try:
            with self.assertRaises(APIStatusError) as caught:
                await client.complete("Hi")
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(client.history, [])
            self.assertEqual(self.outbound, [])
        finally:
            await client.close()


class GatewayProcessTests(unittest.TestCase):
    def test_standalone_listener_without_mongodb_or_browser(self):
        port = free_port()
        with tempfile.TemporaryDirectory(prefix="r2a-gateway-process-") as root:
            env_file = Path(root, ".env")
            env_file.write_text("")
            env = {**os.environ, "GATEWAY_API_KEY": DUMMY_KEY, "GATEWAY_LOG_LEVEL": "error"}
            with Path(root, "gateway.log").open("w+") as log:
                proc = subprocess.Popen([str(ROOT / "run-gateway.sh"), "--env-file", str(env_file),
                                         "--host", "0.0.0.0", "--port", str(port)],
                                        cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    base = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 15
                    with httpx.Client(base_url=base, trust_env=False, timeout=2) as client:
                        while True:
                            if proc.poll() is not None:
                                self.fail("Gateway exited before readiness")
                            try:
                                health = client.get("/health")
                                if health.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if time.monotonic() > deadline:
                                self.fail("Gateway startup timed out")
                            time.sleep(0.1)
                        self.assertFalse(health.json()["extension"]["active"])
                        self.assertEqual(client.get("/v1/models").status_code, 401)
                        headers = {"Authorization": f"Bearer {DUMMY_KEY}"}
                        self.assertEqual(client.get("/v1/models", headers=headers).json()["data"][0]["id"], "waiting-for-extension")
                        response = client.post("/v1/chat/completions", headers=headers,
                                               json={"model": "Demo Text", "messages": [{"role": "user", "content": "Hi"}]})
                        self.assertEqual(response.status_code, 503)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(5)
                log.seek(0)
                self.assertNotIn(DUMMY_KEY, log.read())
                # Recent Uvicorn restores/re-raises the normal termination signal.
                self.assertIn(proc.returncode, (0, -signal.SIGTERM))
                with httpx.Client(trust_env=False, timeout=1) as client:
                    with self.assertRaises(httpx.ConnectError):
                        client.get(f"http://127.0.0.1:{port}/health")


if __name__ == "__main__":
    unittest.main(verbosity=2)
