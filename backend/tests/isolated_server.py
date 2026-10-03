"""Run a private backend for destructive tests.

Starts `uvicorn server:app` (no reload) on a free port with DB_NAME pointing at a
throwaway database (roadmap2arena_test_<hex>) and a short step delay, so tests can
delete "all finished jobs", change settings etc. without touching the dev data.
The database and default temporary repo directory are removed on exit. Real integration
credentials/endpoints are disabled by default; extra_env can supply local stand-ins.

    with IsolatedServer() as base:      # base = "http://127.0.0.1:<port>/api"
        httpx.get(f"{base}/queue")
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import tempfile

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _env_file() -> dict:
    """Effective settings: optional .env defaults, overridden by the process environment."""
    defaults = {k: v for k, v in dotenv_values(os.path.join(BACKEND_DIR, ".env")).items()
                if v is not None}
    return {**defaults, **os.environ}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class IsolatedServer:
    def __init__(self, step_delay: float = 0.2, extra_env: dict | None = None):
        self.db_name = f"roadmap2arena_test_{secrets.token_hex(4)}"
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}/api"
        self.step_delay = step_delay
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self.mongo_url: str | None = None
        self.log_path = f"/tmp/{self.db_name}.log"

    def __enter__(self) -> str:
        self._data_dir = tempfile.TemporaryDirectory(prefix="r2a-test-data-")
        env = {**_env_file(),
               # A test must never inherit real integration credentials/endpoints or job repos.
               "ARENA2API_URL": os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090"),
               "ARENA2API_API_KEY": "",
               "R2A_GITHUB_TOKEN": "", "GITHUB_OAUTH_CLIENT_ID": "",
               "GITHUB_API_URL": "http://127.0.0.1:9", "GITHUB_OAUTH_URL": "http://127.0.0.1:9",
               "R2A_SECRET_KEY": Fernet.generate_key().decode(), "R2A_DATA_DIR": self._data_dir.name,
               **self.extra_env, "DB_NAME": self.db_name,
               "ARENA_STEP_DELAY_SECONDS": str(self.step_delay), "PYTHONDONTWRITEBYTECODE": "1",
               # Never pull a checkout's legacy repos into a throwaway test server.
               "R2A_MIGRATE_LEGACY_DATA": "0"}
        self.mongo_url = env.get("MONGO_URL")
        self._log = open(self.log_path, "w")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(self.port)],
                cwd=BACKEND_DIR, env=env, stdout=self._log, stderr=subprocess.STDOUT)
            end = time.time() + 30
            while time.time() < end:
                if self.proc.poll() is not None:
                    with open(self.log_path) as log:
                        raise RuntimeError(f"isolated backend exited: {log.read()[-2000:]}")
                try:
                    if httpx.get(f"{self.base}/queue", timeout=2).status_code == 200:
                        # Direct-port checks must also stay on this isolated server.
                        self._saved_env = {k: os.environ.get(k) for k in ("TEST_DIRECT_URL", "R2A_TEST_ISOLATED")}
                        os.environ["TEST_DIRECT_URL"] = self.base
                        os.environ["R2A_TEST_ISOLATED"] = "1"
                        return self.base
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
            raise RuntimeError("isolated backend did not start")
        except BaseException:
            # __exit__ is not invoked by a with statement if __enter__ fails.
            self.__exit__(*sys.exc_info())
            raise

    def db(self):
        from pymongo import MongoClient
        return MongoClient(self.mongo_url or _env_file()["MONGO_URL"], serverSelectionTimeoutMS=5000)[self.db_name]

    def __exit__(self, *exc) -> None:
        for k, v in getattr(self, "_saved_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        self._log.close()
        try:
            client = self.db().client
            try:
                client.drop_database(self.db_name)
            finally:
                client.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._data_dir.cleanup()


def run_tests(namespace: dict) -> int:
    """Tiny runner: calls every test_* function in definition order, prints PASS/FAIL."""
    import traceback
    names = [n for n in list(namespace) if n.startswith("test_")]
    passed, failed = 0, []
    for n in names:
        t = time.time()
        try:
            namespace[n]()
            passed += 1
            print(f"PASS {n} ({time.time() - t:.1f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            print(f"FAIL {n}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=3)
    print(f"\n{passed} passed, {len(failed)} failed: {failed}")
    return 1 if failed else 0
