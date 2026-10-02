"""Run a private backend for destructive tests.

Starts `uvicorn server:app` (no reload) on a free port with DB_NAME pointing at a
throwaway database (roadmap2arena_test_<hex>) and a short step delay, so tests can
delete "all finished jobs", change settings etc. without touching the dev data.
The database is dropped on exit. Extra env vars can be passed (e.g. data dirs).

    with IsolatedServer() as base:      # base = "http://127.0.0.1:<port>/api"
        httpx.get(f"{base}/queue")
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time

import httpx

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
UVICORN = "/app/venv/bin/uvicorn"


def _env_file() -> dict:
    return dict(l.strip().split("=", 1) for l in open(os.path.join(BACKEND_DIR, ".env"))
                if "=" in l and not l.lstrip().startswith("#"))


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
        self.log_path = f"/tmp/{self.db_name}.log"

    def __enter__(self) -> str:
        env = {**os.environ, "DB_NAME": self.db_name, "ARENA_STEP_DELAY_SECONDS": str(self.step_delay),
               "PYTHONDONTWRITEBYTECODE": "1", **self.extra_env}
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen([UVICORN, "server:app", "--host", "127.0.0.1", "--port", str(self.port)],
                                     cwd=BACKEND_DIR, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        end = time.time() + 30
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"isolated backend exited: {open(self.log_path).read()[-2000:]}")
            try:
                if httpx.get(f"{self.base}/queue", timeout=2).status_code == 200:
                    # suites that also hit a "direct" URL (default :8001) must use this server instead
                    self._saved_env = {k: os.environ.get(k) for k in ("TEST_DIRECT_URL", "R2A_TEST_ISOLATED")}
                    os.environ["TEST_DIRECT_URL"] = self.base
                    os.environ["R2A_TEST_ISOLATED"] = "1"
                    return self.base
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("isolated backend did not start")

    def db(self):
        from pymongo import MongoClient
        return MongoClient(_env_file()["MONGO_URL"], serverSelectionTimeoutMS=5000)[self.db_name]

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
        self._log.close()
        try:
            self.db().client.drop_database(self.db_name)
        except Exception:  # noqa: BLE001
            pass


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
