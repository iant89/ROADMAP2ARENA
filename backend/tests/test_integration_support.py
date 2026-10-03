"""Self-contained integration-infrastructure/fixture checks; no MongoDB needed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gateway_fixture import MODEL, create_fixture
from integration import reload_sandbox
from integration_support import GatewayFixture, LocalService, ROOT, safe_environment, test_mongo_url

KEY = "synthetic-fixture-key-for-tests-only"


class MongoSafetyTests(unittest.TestCase):
    def test_explicit_loopback_addresses(self):
        for uri in ("mongodb://127.0.0.1:27018/", "mongodb://localhost:27018", "mongodb://[::1]:27018/"):
            self.assertEqual(test_mongo_url(uri), uri)

    def test_missing_test_uri_never_inherits_app_uri(self):
        with patch.dict(os.environ, {"MONGO_URL": "mongodb://localhost:27017/normal_app"}, clear=True):
            with self.assertRaises(ValueError):
                test_mongo_url()

    def test_remote_credentials_database_or_options_rejected(self):
        for uri in ("", "mongodb://remote.test:27017/", "mongodb+srv://localhost/", "mongodb://localhost/",
                    "mongodb://localhost:27017/app", "mongodb://localhost:27017/?authSource=admin",
                    "mongodb://u:synthetic-secret@localhost:27017/", "mongodb://localhost:27017,other:27017/",
                    "mongodb://localhost:0/", "mongodb://[::1:27018/", "mongodb://localhost:65536/"):
            with self.subTest(uri=uri), self.assertRaises(ValueError) as caught:
                test_mongo_url(uri)
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_inert_environment_overrides_real_integration_sentinels(self):
        with patch.dict(os.environ, {"ARENA2API_API_KEY": "do-not-use", "R2A_GITHUB_TOKEN": "do-not-use",
                                     "GITHUB_API_URL": "https://do-not-use.test", "ARENA2API_URL": "https://do-not-use.test"}):
            env = safe_environment("mongodb://127.0.0.1:27018/")
        self.assertEqual(env["ARENA2API_API_KEY"], "")
        self.assertEqual(env["R2A_GITHUB_TOKEN"], "")
        self.assertEqual(env["ARENA2API_URL"], "http://127.0.0.1:9")
        self.assertEqual(env["GITHUB_API_URL"], "http://127.0.0.1:9")
        self.assertEqual(env["MONGO_URL"], "mongodb://127.0.0.1:27018/")

    def test_reload_copy_excludes_runtime_data_and_private_env(self):
        with tempfile.TemporaryDirectory(prefix="r2a-copy-safety-") as root:
            source, target = Path(root, "source"), Path(root, "target")
            for relative in ("backend/tests/test_reload_isolation.py", "backend/.env", "backend/.env.example",
                             "backend/data/repo.py", "backend/__pycache__/x.pyc", "run.sh", "deploy/supervisor/backend.conf"):
                file = source / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("synthetic content")
            reload_sandbox(source, target)
            self.assertTrue((target / "backend/tests/test_reload_isolation.py").exists())
            self.assertTrue((target / "deploy/supervisor/backend.conf").exists())
            self.assertTrue((target / "run.sh").exists())
            self.assertFalse((target / "backend/.env").exists())
            self.assertFalse((target / "backend/data").exists())
            self.assertFalse((target / "backend/__pycache__").exists())
            self.assertTrue((source / "backend/.env").exists())

    def test_driver_refuses_missing_uri_before_starting_services(self):
        env = {k: v for k, v in os.environ.items() if k != "TEST_MONGO_URL"}
        result = subprocess.run([sys.executable, str(HERE / "integration.py")], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TEST_MONGO_URL", result.stderr)
        self.assertNotIn("===", result.stdout)


class OwnedServiceTests(unittest.TestCase):
    def test_command_has_chosen_free_port(self):
        service = LocalService(["python", "server.py", "--port", "{port}"])
        self.assertEqual(service.command[-1], str(service.port))
        self.assertTrue(service.base.startswith("http://127.0.0.1:"))

    def test_failed_start_cleans_process_and_log(self):
        proc = MagicMock()
        proc.poll.return_value = None
        with patch("integration_support.subprocess.Popen", return_value=proc), patch("integration_support.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.side_effect = RuntimeError("synthetic readiness failure")
            service = LocalService(["dummy"])
            with self.assertRaises(RuntimeError):
                service.__enter__()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        self.assertTrue(service.log.closed)

    def test_spawn_failure_closes_log(self):
        service = LocalService(["dummy"])
        with patch("integration_support.subprocess.Popen", side_effect=OSError("synthetic spawn failure")):
            with self.assertRaises(OSError):
                service.__enter__()
        self.assertTrue(service.log.closed)

    def test_hung_shutdown_is_killed_and_reaped(self):
        service = LocalService(["dummy"])
        service.proc = MagicMock()
        service.proc.poll.return_value = None
        service.proc.wait.side_effect = [subprocess.TimeoutExpired("dummy", 10), 0]
        service.__exit__()
        service.proc.kill.assert_called_once()
        self.assertEqual(service.proc.wait.call_count, 2)

    def test_real_fixture_listener_is_guarded_and_stopped(self):
        with GatewayFixture() as service:
            base = service.base
            with httpx.Client(trust_env=False, timeout=5) as client:
                self.assertEqual(client.get(base + "/v1/models").status_code, 401)
                self.assertEqual(client.get(base + "/_test/requests").status_code, 401)
            self.assertEqual(service.control("/_test/requests"), {"requests": []})
            self.assertEqual(service.control("/_test/reset", body={"active": False}), {"ok": True})
        with httpx.Client(trust_env=False, timeout=1) as client:
            with self.assertRaises(httpx.ConnectError):
                client.get(base + "/health")

    def test_fixture_cli_refuses_without_explicit_guard(self):
        env = {k: v for k, v in os.environ.items() if k != "R2A_TEST_GATEWAY"}
        result = subprocess.run([sys.executable, str(HERE / "gateway_fixture.py"), "--port", "9090"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Test-only gateway", result.stderr)


class ComposeLauncherTests(unittest.TestCase):
    def invoke(self, *, child_exit=0, address="127.0.0.1:54321", up_exit=0):
        with tempfile.TemporaryDirectory(prefix="r2a-compose-test-") as root:
            folder = Path(root)
            docker = folder / "docker"
            docker.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if "port" in sys.argv:
    print({address!r})
if "up" in sys.argv:
    sys.exit({up_exit})
''')
            python = folder / "test-python"
            python.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
Path(os.environ["FAKE_CHILD_LOG"]).write_text(os.environ.get("TEST_MONGO_URL", ""))
sys.exit({child_exit})
''')
            docker.chmod(0o755)
            python.chmod(0o755)
            env = {k: v for k, v in os.environ.items() if k != "TEST_MONGO_URL"}
            env.update(PATH=str(folder) + os.pathsep + env["PATH"], R2A_TEST_PYTHON=str(python),
                       FAKE_DOCKER_LOG=str(folder / "docker.log"), FAKE_CHILD_LOG=str(folder / "child.log"))
            result = subprocess.run(["bash", str(ROOT / "test-integration.sh"), "--pipeline-only"],
                                    env=env, capture_output=True, text=True, timeout=10)
            calls = [json.loads(line) for line in (folder / "docker.log").read_text().splitlines()]
            child = (folder / "child.log").read_text() if (folder / "child.log").exists() else None
            return result, calls, child

    def test_ephemeral_compose_project_and_cleanup_on_success(self):
        result, calls, uri = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(uri, "mongodb://127.0.0.1:54321/")
        projects = [args[args.index("--project-name") + 1] for args in calls if "--project-name" in args]
        self.assertEqual(len(set(projects)), 1)
        self.assertTrue(projects[0].startswith("r2a-test-"))
        self.assertIn("down", calls[-1])
        self.assertIn("--volumes", calls[-1])

    def test_failed_test_child_still_removes_compose_project(self):
        result, calls, _ = self.invoke(child_exit=17)
        self.assertEqual(result.returncode, 17)
        self.assertIn("down", calls[-1])

    def test_failed_container_start_is_cleaned_without_running_tests(self):
        result, calls, uri = self.invoke(up_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(uri)
        self.assertIn("down", calls[-1])

    def test_non_loopback_docker_binding_refused_and_cleaned(self):
        result, calls, uri = self.invoke(address="0.0.0.0:54321")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(uri)
        self.assertIn("refusing", result.stderr)
        self.assertIn("down", calls[-1])


class FixtureContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_fixture(KEY)),
                                     base_url="http://fixture.test", headers={"Authorization": f"Bearer {KEY}"}, trust_env=False)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def complete(self, messages):
        return await self.http.post("/v1/chat/completions", json={"model": MODEL, "messages": messages, "stream": False})

    async def test_synthetic_conversion_and_history(self):
        first = (await self.complete([{"role": "user", "content": "Step one"}])).json()
        text = first["choices"][0]["message"]["content"]
        self.assertIn("pipeline turn 1", text)
        second = await self.complete([{"role": "user", "content": "Step one"},
                                      {"role": "assistant", "content": text}, {"role": "user", "content": "Step two"}])
        self.assertIn("pipeline turn 2", second.json()["choices"][0]["message"]["content"])
        trace = (await self.http.get("/_test/requests")).json()["requests"]
        self.assertEqual([x["turn"] for x in trace], [1, 2])
        self.assertNotIn(KEY, json.dumps(trace))

    async def test_failure_is_once_only_and_resettable(self):
        self.assertEqual((await self.http.post("/_test/reset", json={"fail_turn": 1})).status_code, 200)
        messages = [{"role": "user", "content": "Step one"}]
        self.assertEqual((await self.complete(messages)).status_code, 429)
        self.assertEqual((await self.complete(messages)).status_code, 200)
        await self.http.post("/_test/reset", json={"active": False})
        self.assertEqual((await self.complete(messages)).status_code, 503)
        self.assertEqual((await self.http.get("/_test/requests")).json()["requests"], [])

    async def test_controls_reject_invalid_delay_and_turn(self):
        for body in ({"delay": True}, {"delay": 6}, {"delay": -1}, {"fail_turn": True}, {"fail_turn": 0}, {"fail_turn": 11}):
            with self.subTest(body=body):
                self.assertEqual((await self.http.post("/_test/reset", json=body)).status_code, 400)

    async def test_empty_fixture_key_refused(self):
        with self.assertRaises(ValueError):
            create_fixture("")


if __name__ == "__main__":
    unittest.main(verbosity=2)
