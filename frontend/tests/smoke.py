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
        self.github_connected = False
        self.projects = []
        self.project_imports = []
        self.project_repos = [{"full_name": "r2a-tester/existing-repo", "name": "existing-repo", "owner": "r2a-tester",
                               "private": False, "default_branch": "main", "html_url": "https://github.com/r2a-tester/existing-repo",
                               "description": "A test repository", "can_push": True, "updated_at": "2026-10-01T00:00:00Z"}]
        self.providers = [{"id": "local-provider", "name": "Local arena2api", "preset": "arena2api",
                           "base_url": "http://127.0.0.1:9090/v1", "headers": {}, "default_model": "gpt-4o",
                           "api_key_set": False, "api_key_error": None, "server_key": False, "is_default": True,
                           "migrated": True, "created_at": None, "updated_at": None}]
        self.presets = [
            {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True, "hint": "OpenAI API"},
            {"id": "arena2api", "label": "arena2api (local)", "base_url": "http://127.0.0.1:9090/v1", "needs_key": False, "hint": "Local gateway"},
            {"id": "custom", "label": "Custom", "base_url": "", "needs_key": False, "hint": "Custom OpenAI-compatible API"},
        ]
        self.project = {"id": "project-1", "name": "Existing repo", "context": "Use typed routes.",
                        "source": {"repo_full_name": "r2a-tester/existing-repo", "html_url": "https://github.com/r2a-tester/existing-repo",
                                   "private": False, "branch": "main", "default_branch": "main", "remote_updated_at": "2026-10-01T00:00:00Z",
                                   "size_kb": 1},
                        "head": "0123456789abcdef0123456789abcdef01234567", "commit_count": 1, "job_count": 0,
                        "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}

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
        elif path == "/api/providers" and method == "GET":
            reply({"providers": self.providers, "default_provider_id": "local-provider", "presets": self.presets,
                   "encryption": "missing"})
        elif re.fullmatch(r"/api/providers/[^/]+/models", path) and method == "GET":
            reply({"models": ["gpt-4o"], "count": 1})
        elif path == "/api/projects" and method == "GET":
            reply(self.projects)
        elif path == "/api/projects" and method == "POST":
            body = request.post_data_json
            project = deepcopy(self.project)
            project["name"] = body.get("name") or "existing-repo"
            project["context"] = body.get("context", "")
            project["job_count"] = 0
            self.projects.append(project)
            self.project_imports.append(body)
            reply(project, 201)
        elif path == "/api/github/repos" and method == "GET":
            reply({"items": self.project_repos, "total": len(self.project_repos), "truncated": False})
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
            reply({"connected": self.github_connected, "auth_method": "pat" if self.github_connected else None,
                   "source": "settings" if self.github_connected else None, "encryption": "missing", "env_token": False,
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
            expect(page.get_by_test_id("job-provider-select")).to_have_value("local-provider")
            expect(page.get_by_test_id("job-provider-base")).to_have_text("http://127.0.0.1:9090/v1")
            expect(page.get_by_test_id("start-job-button")).to_be_disabled()
            expect(page.get_by_test_id("backend-error-banner")).to_have_count(0)
            for name in ("create", "queue", "current", "history", "projects", "settings"):
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
            expect(page.get_by_test_id("project-selector")).to_have_value("")
            page.get_by_test_id("model-input").fill(" ")
            expect(page.get_by_test_id("job-model-error")).to_have_text("Model is required")
            expect(page.get_by_test_id("model-input")).to_have_attribute("aria-invalid", "true")
            expect(page.get_by_test_id("start-job-button")).to_be_disabled()
            page.get_by_test_id("model-input").fill("gpt-4o")
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
            select_tab(page, "projects")
            expect(page.get_by_test_id("projects-tab")).to_be_visible()
            expect(page.get_by_test_id("projects-empty")).to_be_visible()
            expect(page.get_by_test_id("project-github-disconnected")).to_be_visible()
            check_layout(page, width)
            fixtures.github_connected = True
            page.get_by_test_id("project-check-github").click()
            repo = page.get_by_test_id("project-repo-select")
            expect(repo).to_be_enabled()
            repo.select_option("r2a-tester/existing-repo")
            expect(page.get_by_test_id("project-import-name")).to_have_value("existing-repo")
            page.get_by_test_id("project-import-context").fill("Keep the existing API typed.")
            page.get_by_test_id("project-import-button").click()
            expect(page.get_by_test_id("project-card-project-1")).to_be_visible()
            expect(page.get_by_test_id("project-context-preview")).to_have_text("Keep the existing API typed.")
            assert fixtures.project_imports == [{"repo_full_name": "r2a-tester/existing-repo", "name": "existing-repo",
                                                 "context": "Keep the existing API typed."}]
            page.get_by_test_id("project-use-project-1").click()
            expect(page.get_by_test_id("tab-create")).to_have_attribute("data-state", "active")
            expect(page.get_by_test_id("project-selector")).to_have_value("project-1")
            check_layout(page, width)
            fixtures.github_connected = False
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
