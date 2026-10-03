"""One-shot smoke of real Vite /api forwarding with a local HTTP fixture.

Requires installed frontend dependencies + Node; no MongoDB, browser or provider.
Run from the repository root: ./venv/bin/python frontend/tests/dev_proxy.py
Both owned servers are stopped even if a check fails.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from roadmap_parser import parse_roadmap  # noqa: E402


class Fixture(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *args):
        pass

    def reply(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        self.hits.append(("GET", self.path, self.headers["Host"]))
        if self.path == "/api/config":
            self.reply({"model": "dev-proxy-fixture", "arena_url": "http://127.0.0.1:9090",
                        "step_delay_seconds": 2, "request_timeout_seconds": 300})
        else:
            self.reply({"detail": "fixture route not found"}, 404)

    def do_POST(self):
        self.hits.append(("POST", self.path, self.headers["Host"]))
        if self.path != "/api/roadmap/parse":
            self.reply({"detail": "fixture route not found"}, 404)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        steps = parse_roadmap(payload["roadmap_md"])
        self.reply({"steps": steps})


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    node = shutil.which("node")
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not node or not vite.is_file():
        raise RuntimeError("Install Node and frontend dependencies before running the dev-proxy smoke")
    backend = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    env = {**os.environ, "VITE_BACKEND_URL": "", "R2A_BACKEND_TARGET": f"http://127.0.0.1:{backend.server_port}"}
    proc = None
    try:
        with tempfile.TemporaryFile(mode="w+") as log:
            proc = subprocess.Popen([node, str(vite), "--host", "0.0.0.0", "--port", str(port)],
                                    cwd=ROOT / "frontend", env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5,
                                  headers={"Host": f"{port}-roadmap2arena-test.e2b.app"}) as client:
                    deadline = time.monotonic() + 20
                    while True:
                        if proc.poll() is not None:
                            raise RuntimeError("Vite exited before startup")
                        try:
                            page = client.get("/")
                            if page.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError("Vite startup timed out")
                        time.sleep(0.1)
                    assert "/src/main.jsx" in page.text, "Preview Host must be accepted"
                    print("PASS dev proxy: preview Host accepted by actual Vite")
                    response = client.get("/api/config")
                    assert response.status_code == 200 and response.json()["model"] == "dev-proxy-fixture"
                    print("PASS dev proxy: same-origin GET reaches local fixture")
                    response = client.post("/api/roadmap/parse", json={"roadmap_md": "### Proxied step\nBuild a file"},
                                           headers={"Origin": f"https://{port}-roadmap2arena-test.e2b.app"})
                    assert response.status_code == 200 and response.json()["steps"][0]["title"] == "Proxied step"
                    print("PASS dev proxy: POST body/path reach the real parser fixture")
                    response = client.get("/api/missing")
                    assert response.status_code == 404 and response.json()["detail"] == "fixture route not found"
                    assert all(host == f"127.0.0.1:{backend.server_port}" for _, _, host in Fixture.hits)
                    print("PASS dev proxy: API status/path preserved and target Host rewritten")
                    source = client.get("/src/lib/api.js").text
                    assert re.search(r'"VITE_BACKEND_URL"\s*:\s*""', source), "Browser API base must stay empty"
                    assert env["R2A_BACKEND_TARGET"] not in source, "Proxy target must not be browser-facing"
                    print("PASS dev proxy: empty browser API base; target stays server-side")
            except Exception:
                log.seek(0)
                # Fixture-only diagnostics; no credentials are supplied to these requests.
                print(log.read()[-2000:], file=sys.stderr)
                raise
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)
    finally:
        backend.shutdown()
        backend.server_close()
        thread.join(5)
    with httpx.Client(trust_env=False, timeout=1) as client:
        try:
            client.get(f"http://127.0.0.1:{port}/")
        except httpx.ConnectError:
            pass
        else:
            raise AssertionError("Vite listener was not closed")
    print("5/5 dev-proxy checks passed (local fixture; no persisted jobs)")


if __name__ == "__main__":
    main()
