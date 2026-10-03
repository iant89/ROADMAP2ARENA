"""Owned local test services and explicit throwaway-Mongo validation helpers."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from pymongo import MongoClient

from isolated_server import free_port

ROOT = Path(__file__).resolve().parents[2]


def test_mongo_url(value: str | None = None) -> str:
    """Never silently fall back to the application's production MONGO_URL/.env."""
    uri = os.environ.get("TEST_MONGO_URL", "") if value is None else value
    try:
        parsed = urlsplit(uri)
        valid = (parsed.scheme == "mongodb" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                 and parsed.username is None and parsed.password is None and parsed.path in {"", "/"}
                 and not parsed.query and not parsed.fragment and parsed.port is not None
                 and 1 <= parsed.port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("TEST_MONGO_URL must explicitly name one loopback MongoDB host/port, without credentials, database or options")
    return uri


def require_mongo(uri: str):
    with MongoClient(test_mongo_url(uri), serverSelectionTimeoutMS=3000, connectTimeoutMS=3000) as client:
        client.admin.command("ping")
        return client.server_info()["version"]


def safe_environment(uri: str) -> dict:
    """A test process receives inert config; its backend harness forces a fresh DB."""
    return {**os.environ, "MONGO_URL": test_mongo_url(uri), "TEST_MONGO_URL": uri,
            "DB_NAME": "roadmap2arena_test_driver", "ARENA2API_URL": "http://127.0.0.1:9",
            "ARENA2API_MODEL": "gpt-4o", "ARENA2API_API_KEY": "", "ARENA_STEP_DELAY_SECONDS": "0.2",
            "ARENA_REQUEST_TIMEOUT_SECONDS": "30", "CORS_ORIGINS": "*", "R2A_GITHUB_TOKEN": "",
            "GITHUB_OAUTH_CLIENT_ID": "", "GITHUB_API_URL": "http://127.0.0.1:9",
            "GITHUB_OAUTH_URL": "http://127.0.0.1:9", "R2A_SECRET_KEY": "", "R2A_MIGRATE_LEGACY_DATA": "0",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}


class LocalService:
    """Bounded startup/stop; failed __enter__ also terminates its owned process."""
    def __init__(self, command, *, cwd=ROOT, env=None, ready_path="/health", headers=None):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.command = [str(arg).replace("{port}", str(self.port)) for arg in command]
        self.cwd, self.env, self.ready_path, self.headers = cwd, env, ready_path, headers or {}
        self.proc = None
        self.log = None

    def __enter__(self):
        self.log = tempfile.TemporaryFile(mode="w+")
        try:
            self.proc = subprocess.Popen(self.command, cwd=self.cwd, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 20
            with httpx.Client(trust_env=False, timeout=1, headers=self.headers) as client:
                while time.monotonic() < deadline:
                    if self.proc.poll() is not None:
                        raise RuntimeError("Local test service exited before readiness")
                    try:
                        if client.get(self.base + self.ready_path).status_code == 200:
                            return self
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
            raise RuntimeError("Local test service startup timed out")
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *_):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(5)
        finally:
            if self.log:
                self.log.close()


class GatewayFixture(LocalService):
    def __init__(self):
        self.key = secrets.token_urlsafe(32)
        env = {**os.environ, "R2A_TEST_GATEWAY": "1", "TEST_GATEWAY_KEY": self.key}
        super().__init__([sys.executable, ROOT / "backend/tests/gateway_fixture.py", "--port", "{port}"], env=env)

    def control(self, path, *, body=None):
        with httpx.Client(trust_env=False, timeout=10, headers={"Authorization": f"Bearer {self.key}"}) as client:
            response = client.get(self.base + path) if body is None else client.post(self.base + path, json=body)
            response.raise_for_status()
            return response.json()
