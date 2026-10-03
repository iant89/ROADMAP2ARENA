"""Smoke-test the production frontend with explicit, in-memory API fixtures.

No MongoDB, live gateway, GitHub account, or notification provider is used.
Roadmap previews call the real Python parser. This is NOT a backend lifecycle test.

    (cd frontend && yarn build)
    ./venv/bin/python frontend/tests/smoke.py [--browser /path/to/chromium]
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
import traceback
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from roadmap_parser import parse_roadmap, roadmap_title  # noqa: E402

CFG = {"arena_url": "http://127.0.0.1:9090", "model": "gpt-4o",
       "step_delay_seconds": 2, "request_timeout_seconds": 300}
EVENTS = ["job_done", "job_failed", "job_stopped", "queue_empty", "github_pushed", "github_pr_opened",
          "github_pr_merged", "github_pr_closed", "github_checks_passed", "github_checks_failed"]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Fixtures:
    def __init__(self, base):
        self.base = base
        self.settings = deepcopy(CFG)
        self.unexpected = []
        self.requests = []

    def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        # Block fonts/external assets and all external-service URLs before network dispatch.
        if not request.url.startswith(self.base + "/"):
            if request.resource_type in ("fetch", "xhr"):
                self.unexpected.append(f"Wrong-origin API request: {request.url}")
            route.abort()
            return
        path, method = url.path, request.method
        if not path.startswith("/api/"):
            route.continue_()
            return
        self.requests.append((method, path))

        def reply(data, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

        if path in ("/api/config", "/api/settings"):
            if method == "PUT":
                self.settings.update(request.post_data_json)
            reply({**self.settings, "updated_at": None, "env_defaults": CFG})
        elif path == "/api/settings/reset" and method == "POST":
            self.settings = deepcopy(CFG)
            reply({**self.settings, "updated_at": None, "env_defaults": CFG})
        elif path == "/api/queue" and method == "GET":
            reply({"running": None, "queued": [], "count": 0, "waiting": 0})
        elif path == "/api/jobs" and method == "GET":
            reply([])
        elif re.fullmatch(r"/api/jobs/[^/]+", path) and method == "GET":
            reply({"detail": "Job not found"}, 404)
        elif path == "/api/roadmap/parse" and method == "POST":
            text = request.post_data_json["roadmap_md"]
            steps = parse_roadmap(text)
            reply({"steps": steps, "title": roadmap_title(text)} if steps else
                  {"detail": "No steps found (use ### headings or - [ ] items)"}, 200 if steps else 422)
        elif path == "/api/notifications" and method == "GET":
            reply({"items": [], "unread_count": 0, "total": 0})
        elif path == "/api/notifications/settings" and method == "GET":
            events = {key: True for key in EVENTS}
            reply({"app_url": "", "updated_at": None, "in_app": {"events": events},
                   "webhook": {"enabled": False, "url": "", "events": events},
                   "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "",
                             "from_addr": "", "to_addrs": [], "events": events,
                             "password_set": False, "password_masked": ""}})
        elif path == "/api/github" and method == "GET":
            reply({"connected": False, "auth_method": None, "source": None, "encryption": "missing", "env_token": False,
                   "token_error": None, "username": None, "name": None, "avatar_url": None, "html_url": None,
                   "scopes": [], "token_type": None, "connected_at": None,
                   "auto_push": {"enabled": False, "private": True},
                   "oauth": {"available": False, "client_id": None, "client_id_source": None, "pending": None}})
        elif path == "/api/github/watches" and method == "GET":
            reply({"items": [], "poll_seconds": 60, "paused_for": 0, "pause_reason": None})
        else:
            self.unexpected.append(f"Unhandled fixture: {method} {path}")
            reply({"detail": "Unexpected API request in smoke test"}, 501)


def check_layout(page, width):
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1, "Page overflows horizontally"


def select_tab(page, name):
    tab = page.get_by_test_id(f"tab-{name}")
    tab.click()
    expect(tab).to_have_attribute("data-state", "active")
    expect(page).to_have_url(re.compile(rf"[?&]tab={name}(?:&|$)"))


def run_viewport(browser, base, width, height):
    label = "mobile" if width < 640 else "desktop"
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 640, has_touch=width < 640)
    fixtures = Fixtures(base)
    ctx.route("**/*", fixtures.route)
    page = ctx.new_page()
    page.set_default_timeout(10_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    passed = 0

    def flow(name, fn):
        nonlocal passed
        fn()
        passed += 1
        print(f"PASS {label}: {name}", flush=True)

    try:
        def load():
            page.goto(base + "/")
            expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
            expect(page.get_by_test_id("arena-url-input")).to_have_value(CFG["arena_url"])
            expect(page.get_by_test_id("start-job-button")).to_be_disabled()
            expect(page.get_by_test_id("backend-error-banner")).to_have_count(0)
            for name in ("create", "queue", "current", "history", "settings"):
                tab = page.get_by_test_id(f"tab-{name}")
                expect(tab).to_be_visible()
                assert tab.get_attribute("aria-label"), "Tab needs an accessible name"
                box = tab.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
            check_layout(page, width)
        flow("load/defaults/accessibility", load)

        def validation():
            page.get_by_test_id("load-sample-button").click()
            expect(page.get_by_test_id("steps-found")).to_have_text(re.compile(r"^[1-9]\d* steps? found$"))
            expect(page.get_by_test_id("start-job-button")).to_be_enabled()
            page.get_by_test_id("model-input").fill(" ")
            expect(page.get_by_test_id("model-error")).to_have_text("Model is required")
            expect(page.get_by_test_id("model-input")).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_test_id("start-job-button")).to_be_disabled()
            page.get_by_test_id("arena-url-input").fill("ftp://invalid.test")
            expect(page.get_by_test_id("arena_url-error")).to_have_text("Use an http:// or https:// URL")
            page.get_by_test_id("model-input").fill("gpt-4o")
            page.get_by_test_id("arena-url-input").fill(CFG["arena_url"])
            expect(page.get_by_test_id("start-job-button")).to_be_enabled()
            assert ("POST", "/api/jobs") not in fixtures.requests, "Validation must not submit a job"
        flow("sample/inline validation", validation)

        def fenced_preview():
            page.get_by_test_id("roadmap-input").fill("# Demo\n```md\n### Example only\n- [ ] Example task\n```\n### Real\nGo\n")
            expect(page.get_by_test_id("steps-found")).to_have_text("1 step found")
            expect(page.get_by_test_id("start-job-button")).to_be_enabled()
        flow("fenced-example parser regression", fenced_preview)

        def navigation():
            for name, empty in (("queue", "queue-empty"), ("current", "current-empty"), ("history", "history-empty")):
                select_tab(page, name)
                expect(page.get_by_test_id(empty)).to_be_visible()
                check_layout(page, width)
            select_tab(page, "settings")
            expect(page.get_by_test_id("settings-tab")).to_be_visible()
            page.reload()
            expect(page.get_by_test_id("tab-settings")).to_have_attribute("data-state", "active")
            expect(page.get_by_test_id("setting-model-input")).to_have_value("gpt-4o")
            check_layout(page, width)
        flow("tabs/empty states/URL reload", navigation)

        def settings():
            field = page.get_by_test_id("setting-step_delay_seconds-input")
            field.fill("601")
            expect(page.get_by_test_id("setting-step_delay_seconds-error")).to_be_visible()
            expect(page.get_by_test_id("settings-save")).to_be_disabled()
            field.fill("3")
            page.get_by_test_id("settings-save").click()
            expect(page.get_by_test_id("settings-save")).to_be_disabled()
            page.reload()
            expect(page.get_by_test_id("setting-step_delay_seconds-input")).to_have_value("3")
            page.get_by_test_id("settings-reset").click()
            expect(page.get_by_test_id("settings-reset-confirm")).to_be_visible()
            page.get_by_test_id("settings-reset-yes").click()
            expect(page.get_by_test_id("setting-step_delay_seconds-input")).to_have_value("2")
            expect(page.get_by_test_id("notif-smtp-password")).to_have_attribute("type", "password")
            expect(page.get_by_test_id("gh-token-input")).to_have_attribute("type", "password")
            expect(page.get_by_test_id("gh-connect")).to_be_disabled()
            expect(page.get_by_test_id("gh-key-missing")).to_be_visible()
            expect(page.get_by_test_id("gh-key-missing")).to_contain_text("./venv/bin/python")
            check_layout(page, width)
        flow("settings validation/save/reset/password inputs (mock API)", settings)

        def notifications():
            page.get_by_test_id("notif-bell").click()
            expect(page.get_by_test_id("notif-panel")).to_be_visible()
            expect(page.get_by_test_id("notif-empty")).to_be_visible()
            expect(page.get_by_test_id("notif-mark-all")).to_be_disabled()
            page.keyboard.press("Escape")
            expect(page.get_by_test_id("notif-panel")).not_to_be_visible()
        flow("notification empty state", notifications)

        def missing_job():
            page.goto(base + "/?tab=history&job=00000000-0000-4000-8000-000000000000")
            expect(page.get_by_test_id("history-detail-loading")).to_contain_text("Could not load job")
            expect(page.get_by_test_id("history-detail-loading")).to_contain_text("not found")
            expect(page.get_by_text(re.compile("Lost contact with the backend"))).to_have_count(0)
            expect(page.get_by_test_id("backend-error-banner")).to_have_count(0)
            check_layout(page, width)
        flow("bad deep link without connectivity warning", missing_job)

        assert not errors, f"Page exceptions: {errors}"
        assert not fixtures.unexpected, fixtures.unexpected
        print(f"{label}: {passed}/7 flows, no page exceptions, API requests same-origin", flush=True)
        return passed
    finally:
        ctx.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional Chromium executable; defaults to Playwright's installed Chromium")
    args = parser.parse_args()
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        parser.error("Build the frontend first: (cd frontend && yarn build)")
    server = ThreadingHTTPServer(("0.0.0.0", 0), partial(QuietHandler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser, args=["--no-sandbox", "--disable-dev-shm-usage", "--no-zygote"])
            try:
                passed = sum(run_viewport(browser, base, w, h) for w, h in ((1280, 800), (390, 844)))
                print(f"\n{passed}/14 smoke flows passed (mock API; not a database integration run)")
            finally:
                browser.close()
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
