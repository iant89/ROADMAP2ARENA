"""ROADMAP2ARENA e2e for PRs #6-#9 (deletion, alerts, git, GitHub UI) + regression.
Usage: PYTHONDONTWRITEBYTECODE=1 python e2e_pr9.py <desktop|mobile> flow [flow...]
State in /tmp/r2a4 (files under /app/frontend trigger Vite reloads).
Safety: every request to *github.com / *githubusercontent.com is aborted; every browser call that would make the
backend talk to GitHub (token connect with a well-formed token, OAuth start/poll, repo list, push, poll) is
fulfilled by a local stub in this script. Bulk "read all", "clear all" and "delete all finished" are translated to
this run's own alerts/jobs so pre-existing data is never touched."""
import json, os, random, re, string, sys, time, zipfile, urllib.request, urllib.parse
from collections import Counter
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8080"
DIR = "/tmp/r2a4"; STATE = f"{DIR}/state.json"; SHOTS = "/tmp/frontend-test"
IGNORE = re.compile(r"\[vite\]|React DevTools|favicon\.ico", re.I)
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
FAKE_TOKEN = "ghp_test_fake0000000000000000000000000000"
SMTP_PW = "test_pw_" + "Zq7x" * 3

state = json.load(open(STATE)) if os.path.exists(STATE) else {
    "jobs": {}, "all": [], "suffix": "".join(random.choices(string.ascii_lowercase + string.digits, k=5)),
    "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "github_out": [], "pushes": []}
def save(): json.dump(state, open(STATE, "w"), indent=1)
SUF = state["suffix"]; save()
MODE = sys.argv[1]; MOBILE = MODE == "mobile"
console, netfail, api_bad, wrong = Counter(), Counter(), Counter(), Counter()
info = []
BEFORE_NOTIF_IDS = {n["id"] for n in json.load(open(f"{DIR}/before_notifs.json"))["items"]}

def api(path, method="GET", body=None, raw=False):
    req = urllib.request.Request(BASE + "/api" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read() if raw else json.load(r)

def my_ids(): return set(state["all"])
def own_notifs(unread_only=False):
    items = api("/notifications?limit=200")["items"]
    mine = [n for n in items if n["id"] not in BEFORE_NOTIF_IDS and (n.get("job_id") in my_ids() or (n["event"] == "queue_empty" and n["created_at"] >= state["start"]))]
    return [n for n in mine if not unread_only or not n["read"]]

# ------------------------------------------------------------------ GitHub stub (browser side only)
def gh_base():
    g = json.load(open(f"{DIR}/before_github.json")); return dict(g)

class FakeGH:
    def __init__(self): self.stub = False; self.accept = False; self.method = None; self.client_id = None; self.pending = None; self.auto = {"enabled": False, "private": True}
    def status(self):
        g = gh_base(); g["auto_push"] = dict(self.auto)
        g["oauth"] = {"available": bool(self.client_id), "client_id": self.client_id, "client_id_source": "settings" if self.client_id else None, "pending": self.pending}
        if self.method:
            g.update(connected=True, auth_method=self.method, source="db", username="r2a-fake-tester", name="Fake Tester", avatar_url=None,
                     html_url="http://127.0.0.1:9/r2a-fake-tester", scopes=["repo"] if self.method == "pat" else ["repo"], token_type="classic" if self.method == "pat" else "oauth",
                     connected_at="2026-10-02T05:00:00Z")
        return g

def install_routes(ctx, fake):
    def ext(route, request):
        state["github_out"].append(f"{request.method} {request.url[:100]}"); save(); route.abort()
    ctx.route(re.compile(r"^https?://([a-z0-9-]+\.)*(github\.com|githubusercontent\.com)(/|$)"), ext)

    def gh(route, request):
        path = urllib.parse.urlparse(request.url).path; m = request.method
        body = {}
        try: body = request.post_data_json or {}
        except Exception: pass
        J = lambda data, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(data))
        if path == "/api/github" and m == "GET":
            return J(fake.status()) if fake.stub else route.continue_()
        if path == "/api/github/token" and m == "PUT":
            tok = (body.get("token") or "").strip()
            if not TOKEN_RE.match(tok): return route.continue_()          # backend rejects locally with 422, no GitHub call
            info.append("intercepted PUT /api/github/token (well-formed fake token)")
            if fake.accept:
                fake.method = "pat"; return J(fake.status())
            return J({"detail": "GitHub rejected the token (401 Bad credentials) - check it and try again"}, 401)
        if path == "/api/github" and m == "DELETE":
            fake.method = None; return J(fake.status())
        if path == "/api/github/settings":
            if "oauth_client_id" in body: fake.client_id = body["oauth_client_id"] or None
            if body.get("auto_push"): fake.auto.update({k: v for k, v in body["auto_push"].items() if v is not None})
            return J(fake.status())
        if path == "/api/github/oauth/start":
            if fake.method: return J({"detail": "GitHub is already connected with a personal access token - disconnect it first"}, 409)
            fake.pending = {"user_code": "TEST-0000", "verification_uri": "http://127.0.0.1:9/login/device", "interval": 5, "expires_at": "2026-10-02T06:00:00Z"}
            return J(fake.pending)
        if path == "/api/github/oauth/poll":
            return J({"status": "pending", "interval": 5, "github": fake.status()})
        if path == "/api/github/oauth/cancel":
            fake.pending = None; return J(fake.status())
        if path == "/api/github/repos":
            return J({"items": [{"full_name": "r2a-fake-tester/test-repo", "private": True, "can_push": True, "default_branch": "main", "html_url": "http://127.0.0.1:9/x"},
                                {"full_name": "someone/read-only", "private": False, "can_push": False, "default_branch": "main", "html_url": "http://127.0.0.1:9/y"}], "total": 2, "truncated": False})
        if path == "/api/github/poll":
            return J({"polled": 0})
        return route.continue_()   # GET /api/github/watches (local only)
    ctx.route(re.compile(r"/api/github(/|\?|$)"), gh)

    def jobgh(route, request):
        path = urllib.parse.urlparse(request.url).path
        if path.endswith("/github/push"):
            b = request.post_data_json or {}; state["pushes"].append(b); save()
            if "@" in (b.get("branch") or "") or "//" in (b.get("branch") or ""):
                return route.fulfill(status=422, content_type="application/json", body=json.dumps({"detail": f"'{b['branch']}' is not a valid branch name"}))
            return route.abort("failed")       # never let a push reach the backend
        if fake.stub:
            r = route.fetch(); d = r.json(); d.update(connected=True, username="r2a-fake-tester")
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(d))
        return route.continue_()
    ctx.route(re.compile(r"/api/jobs/[^/]+/github(/push)?$"), jobgh)

    def notif_bulk(route, request):
        path = urllib.parse.urlparse(request.url).path
        if path == "/api/notifications/read-all":
            mine = own_notifs(True)
            for n in mine: api(f"/notifications/{n['id']}/read", "POST")
            info.append(f"read-all translated to {len(mine)} own alerts")
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"updated": len(mine), "unread_count": api('/notifications?limit=1')['unread_count']}))
        if path == "/api/notifications" and request.method == "DELETE":
            mine = own_notifs()
            for n in mine: api(f"/notifications/{n['id']}", "DELETE")
            info.append(f"clear-all translated to {len(mine)} own alerts")
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"deleted": len(mine), "unread_count": api('/notifications?limit=1')['unread_count']}))
        return route.continue_()
    ctx.route(re.compile(r"/api/notifications(/read-all)?$"), notif_bulk)

    def del_finished(route, request):
        fin = [j["job_id"] for j in api("/jobs?limit=200") if j["job_id"] in my_ids() and j["status"] in ("done", "error", "stopped", "cancelled")]
        info.append(f"delete-finished translated to bulk-delete of {len(fin)} own finished jobs")
        if not fin: return route.fulfill(status=200, content_type="application/json", body=json.dumps({"deleted": [], "deleted_count": 0, "skipped": []}))
        res = api("/jobs/bulk-delete", "POST", {"job_ids": fin})
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(res))
    ctx.route(re.compile(r"/api/jobs/delete-finished$"), del_finished)

def instrument(page):
    page.set_default_timeout(10_000)
    page.on("console", lambda m: m.type in ("error", "warning") and not IGNORE.search(m.text) and console.update([f"{m.type}: {m.text[:170]}"]))
    page.on("pageerror", lambda e: console.update([f"pageerror: {str(e)[:170]}"]))
    page.on("requestfailed", lambda r: not IGNORE.search(r.url) and netfail.update([f"{r.method} {re.sub(r'[0-9a-f-]{36}', '<id>', r.url.split('?')[0])} {r.failure}"]))
    def onresp(r):
        if "/api/" in r.url and r.status >= 400:
            api_bad.update([f"{r.request.method} {re.sub(r'[0-9a-f-]{36}', '<id>', r.url.split('?')[0])} -> {r.status}"])
    page.on("response", onresp)
    def onreq(r):
        u = r.url
        if u.startswith(("data:", "blob:")) or "fonts.g" in u: return
        if not u.startswith(BASE): wrong.update([u[:110]])
        elif r.resource_type in ("fetch", "xhr") and "/api/" not in u: wrong.update(["no /api prefix: " + u[:110]])
    page.on("request", onreq)

# ------------------------------------------------------------------ helpers
def roadmap(n, title): return f"# {title}\n\n" + "\n".join(f"### test step {i}\nCreate file {i}.\n" for i in range(1, n + 1))
def tab(page, name):
    page.get_by_test_id(f"tab-{name}").click(); expect(page).to_have_url(re.compile(rf"tab={name}"))
def track(jid, key):
    state["jobs"][key] = jid; state["all"].append(jid); save()

def create_job(page, key, model, n):
    title = f"test_{SUF} {key}"
    tab(page, "create")
    page.get_by_test_id("model-input").fill(model)
    page.get_by_test_id("roadmap-input").fill(roadmap(n, title))
    expect(page.get_by_test_id("steps-found")).to_have_text(f"{n} step{'s' if n > 1 else ''} found")
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
        page.get_by_test_id("start-job-button").click()
    assert resp.value.status == 201, f"create {key}: {resp.value.status} {resp.value.text()[:200]}"
    d = resp.value.json(); track(d["job_id"], key); return d

def J(key): return state["jobs"][key]
def job_status(key): return api(f"/jobs/{J(key)}")["status"]
def wait_status(key, statuses, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        s = job_status(key)
        if s in statuses: return s
        time.sleep(0.5)
    raise AssertionError(f"{key} never reached {statuses} (last {s})")
def wait_idle(timeout=100):
    end = time.time() + timeout
    while time.time() < end:
        q = api("/queue")
        if not q["running"] and not q["waiting"]: return
        time.sleep(1)
    raise AssertionError("queue never went idle")
def clickable(loc): loc.scroll_into_view_if_needed(); loc.click(trial=True)
def rows(page): return page.locator("li[data-testid^=queue-row-]")
def row(page, key): return page.get_by_test_id(f"queue-row-{J(key)}")
def order(page): return [e.get_attribute("data-testid")[10:] for e in rows(page).all()]
def toast(page, text, timeout=15_000):
    expect(page.locator("[data-sonner-toast]").filter(has_text=text).first).to_be_visible(timeout=timeout)
def open_detail(page, key):
    page.goto(f"{BASE}/?tab=history&job={J(key)}"); expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"test_{SUF} {key}")
def dl(page, btn, timeout=20_000):
    with page.expect_download(timeout=timeout) as d: btn.click()
    p = d.value.path(); return d.value.suggested_filename, os.path.getsize(p), p
def overflow(page): return page.evaluate("document.documentElement.scrollWidth") - page.evaluate("innerWidth")
def unnamed_buttons(page):
    return page.evaluate("""() => [...document.querySelectorAll('button, a[href], [role=button], [role=tab]')].filter(e => {
        const r = e.getBoundingClientRect(); if (!r.width || !r.height) return false;
        const lab = (e.getAttribute('aria-label') || '').trim() || (e.getAttribute('title') || '').trim();
        const by = e.getAttribute('aria-labelledby'); const byText = by ? by.split(' ').map(i => document.getElementById(i)?.innerText || '').join('').trim() : '';
        const sr = [...e.querySelectorAll('svg[aria-label], img[alt]')].map(s => s.getAttribute('aria-label') || s.getAttribute('alt')).join('').trim();
        return !(lab || byText || (e.innerText || '').trim() || sr);
      }).map(e => (e.getAttribute('data-testid') || e.tagName.toLowerCase() + '.' + (e.className || '').toString().split(' ').slice(0,3).join('.')) + ' in ' + (e.closest('[data-testid]')?.getAttribute('data-testid') || '?'))""")

# ------------------------------------------------------------------ retests
def flow_r_f004(page):
    for bad in ("not-a-real-id", "00000000-0000-4000-8000-000000000000"):
        page.goto(f"{BASE}/?tab=history&job={bad}")
        expect(page.get_by_text(re.compile("Could not load job|not found", re.I)).first).to_be_visible()
        page.wait_for_load_state("networkidle")
        texts = [t.inner_text() for t in page.locator("[data-sonner-toast]").all()]
        assert not any("Lost contact" in t for t in texts), f"backend-lost toast for {bad}: {texts}"
        page.reload(); expect(page.get_by_text(re.compile("Could not load job|not found", re.I)).first).to_be_visible()
        assert not any("Lost contact" in t.inner_text() for t in page.locator("[data-sonner-toast]").all())

def flow_r_layout(page):
    """F-003/V-001/V-002/V-004/V-005/V-006/V-007 measurable checks."""
    done = state["jobs"].get("R1") or [j["job_id"] for j in json.load(open(f"{DIR}/before_jobs.json")) if j["status"] == "done"][0]
    page.goto(f"{BASE}/?tab=history&job={done}"); expect(page.get_by_test_id("history-detail")).to_be_visible()
    page.wait_for_load_state("networkidle")
    probs = []
    if overflow(page) > 1: probs.append(f"detail page overflows by {overflow(page)}px")
    for t in ("transcript", "files", "steps", "git", "log"):
        loc = page.get_by_test_id(f"detail-tab-{t}"); clickable(loc); loc.click()
        expect(loc).to_have_attribute("data-state", "active")
        bb = loc.bounding_box()
        if bb["x"] < 0 or bb["x"] + bb["width"] > VIEWPORTS[MODE]["width"] + 1: probs.append(f"detail tab {t} outside viewport")
    if overflow(page) > 1: probs.append(f"detail page overflows by {overflow(page)}px after tab clicks")
    zips = page.locator("[data-testid=download-zip-button]:visible").count()
    if zips != 1: probs.append(f"V-004: {zips} visible Download ZIP buttons on a done job")
    for t in ("create", "queue", "current", "history", "settings"):
        bb = page.get_by_test_id(f"tab-{t}").bounding_box()
        if bb["x"] + bb["width"] > VIEWPORTS[MODE]["width"] + 1: probs.append(f"V-001: main tab {t} off-screen")
    if MOBILE:
        h = page.locator("header").first.bounding_box()["height"]
        info.append(f"mobile header height {h:.0f}px")
        if h > 100: probs.append(f"V-006: header {h:.0f}px tall")
    page.goto(f"{BASE}/?tab=history"); expect(page.get_by_test_id("history-filters")).to_be_visible()
    tops = page.evaluate("[...document.querySelectorAll('[data-testid^=history-filter-]')].map(e => Math.round(e.getBoundingClientRect().top))")
    if len(set(tops)) != 1: probs.append(f"V-005: filter chips on {len(set(tops))} rows")
    if overflow(page) > 1: probs.append(f"history list overflows by {overflow(page)}px")
    page.goto(f"{BASE}/?tab=create"); expect(page.get_by_test_id("model-input")).to_be_visible()
    sizes = page.evaluate("['arena-url-input','model-input','project-context-input','roadmap-input'].map(t => getComputedStyle(document.querySelector(`[data-testid=${t}]`)).fontSize)")
    want = "16px" if MOBILE else "14px"
    if set(sizes) != {want}: probs.append(f"V-007: form font sizes {sizes}, expected {want}")
    assert not probs, "; ".join(probs)

def flow_r_f005_and_delete_guards(page):
    """Long running R1 + queued Q1/Q2: '#N in queue', delete blocked while running, queue Cancel, delete queued."""
    wait_idle()
    page.goto(BASE + "/?tab=create"); expect(page.get_by_test_id("model-input")).not_to_have_value("")
    assert create_job(page, "R1", "stub-slow", 2)["status"] == "running"
    for k in ("Q1", "Q2"): assert create_job(page, k, "gpt-4o", 2)["status"] == "queued"
    page.goto(BASE + "/?tab=history")
    expect(page.get_by_test_id(f"history-job-{J('Q1')}")).to_contain_text("#1 in queue")
    expect(page.get_by_test_id(f"history-job-{J('Q2')}")).to_contain_text("#2 in queue")
    # deletion blocked while running
    open_detail(page, "R1")
    expect(page.get_by_test_id("delete-job-button")).to_be_disabled()
    expect(page.get_by_test_id("delete-running-hint")).to_be_visible()
    expect(page.get_by_test_id("push-github-button")).to_be_disabled()
    if not MOBILE or True:
        page.goto(BASE + "/?tab=history")
        page.get_by_test_id("history-select-toggle").click()
        r1 = page.get_by_test_id(f"history-job-{J('R1')}")
        expect(r1).to_have_attribute("aria-disabled", "true")
        r1.click(force=True); expect(page.get_by_test_id("history-selected-count")).to_have_text("0 selected")
    code = None
    try: api(f"/jobs/{J('R1')}", "DELETE")
    except urllib.error.HTTPError as e: code = e.code
    assert code == 409, f"API delete of running job returned {code}"
    # queue: cancel Q2 (two-step), then delete queued Q1 from its detail page
    page.goto(BASE + "/?tab=queue")
    row(page, "Q2").get_by_test_id("queue-remove").click()
    row(page, "Q2").get_by_test_id("queue-remove-yes").click()
    expect(row(page, "Q2")).to_have_count(0)
    assert job_status("Q2") == "cancelled"
    open_detail(page, "Q1")
    b = page.get_by_test_id("delete-job-button"); b.click(); expect(b).to_have_attribute("data-armed", "true"); b.click()
    expect(page.get_by_test_id("history-detail")).to_have_count(0)
    code = None
    try: api(f"/jobs/{J('Q1')}")
    except urllib.error.HTTPError as e: code = e.code
    assert code == 404, f"deleted queued job still there ({code})"
    tab(page, "queue"); expect(page.get_by_test_id("queue-empty")).to_be_visible()
    assert job_status("R1") == "running", "R1 finished before checks completed"

def flow_toasts_done(page):
    """R1 finishes -> 'Job finished' toast + queue-empty toast, bell count rises."""
    page.goto(BASE + "/?tab=current")
    expect(page.get_by_test_id("job-view-title")).to_contain_text(f"test_{SUF} R1", timeout=15_000)
    before = int(page.get_by_test_id("notif-unread-count").inner_text()) if page.get_by_test_id("notif-unread-count").count() else 0
    toast(page, f"Job finished: test_{SUF} R1", timeout=60_000)
    toast(page, "Queue empty", timeout=15_000)
    expect(page.get_by_test_id("current-empty-title")).to_have_text("Waiting for next job", timeout=15_000)
    expect(page.get_by_test_id("notif-unread-count")).not_to_have_text(str(before))
    api_unread = api("/notifications?limit=1")["unread_count"]
    expect(page.get_by_test_id("notif-unread-count")).to_have_text(str(api_unread) if api_unread < 100 else "99+", timeout=8000)

def flow_toasts_fail_stop(page):
    wait_idle()
    page.goto(BASE + "/?tab=create"); expect(page.get_by_test_id("model-input")).not_to_have_value("")
    create_job(page, "S1", "stub-slow", 3)
    expect(page.get_by_test_id("job-view-status")).to_have_text(re.compile("Running", re.I))
    page.get_by_test_id("stop-job-button").click(); page.get_by_test_id("confirm-stop-button").click()
    wait_status("S1", ["stopped"], 20)
    toast(page, f"test_{SUF} S1")
    create_job(page, "E1", "stub-503-at-3", 4)
    wait_status("E1", ["error"], 60)
    toast(page, f"test_{SUF} E1", timeout=20_000)
    t = page.locator("[data-sonner-toast]").filter(has_text=f"test_{SUF} E1").first.inner_text()
    info.append(f"{MODE} failed toast: {t[:120]!r}")
    assert re.search(r"fail|step 3", t, re.I), t

def flow_bell(page):
    page.goto(BASE + "/?tab=create")
    bell = page.get_by_test_id("notif-bell"); clickable(bell)
    expect(bell).to_have_attribute("aria-label", re.compile("Notifications"))
    api_unread = api("/notifications?limit=1")["unread_count"]
    expect(page.get_by_test_id("notif-unread-count")).to_have_text(str(api_unread) if api_unread < 100 else "99+")
    bell.click(); panel = page.get_by_test_id("notif-panel"); expect(panel).to_be_visible()
    mine = own_notifs(); assert len(mine) >= 3, f"expected own alerts, got {len(mine)}"
    unread_mine = [n for n in mine if not n["read"]]
    n0 = unread_mine[0]
    item = page.get_by_test_id(f"notif-item-{n0['id']}")
    expect(item).to_have_attribute("data-read", "false")
    item.get_by_test_id("notif-mark-read").click()
    expect(item).to_have_attribute("data-read", "true")
    assert next(n for n in api("/notifications?limit=200")["items"] if n["id"] == n0["id"])["read"]
    expect(page.get_by_test_id("notif-unread-count")).to_have_text(str(api_unread - 1))
    # mark all read (translated to own alerts) and remove one own alert
    page.get_by_test_id("notif-mark-all").click()
    expect(page.get_by_test_id(f"notif-item-{unread_mine[-1]['id']}")).to_have_attribute("data-read", "true")
    assert not own_notifs(True), "own alerts still unread after mark all"
    victim = mine[-1]["id"]
    page.get_by_test_id(f"notif-item-{victim}").get_by_test_id("notif-remove").click()
    expect(page.get_by_test_id(f"notif-item-{victim}")).to_have_count(0)
    # clear (two-click; translated to own alerts)
    c = page.get_by_test_id("notif-clear"); c.click(); expect(c).to_have_attribute("data-armed", "true"); c.click()
    page.wait_for_timeout(500)  # optimistic empty state, then the 4 s poll refreshes
    assert not own_notifs(), "own alerts remain after clear"
    expect(page.get_by_test_id(f"notif-item-{n0['id']}")).to_have_count(0, timeout=8000)
    left = api("/notifications?limit=200")
    assert BEFORE_NOTIF_IDS <= {n["id"] for n in left["items"]}, "pre-existing alerts were removed!"
    # clicking an alert opens its job (use a fresh own alert if any; else skip)
    info.append(f"{MODE} bell: unread {api_unread}, own alerts handled {len(mine)}")

def flow_notif_settings(page):
    before = api("/notifications/settings")
    page.goto(BASE + "/?tab=settings"); s = page.get_by_test_id("notif-settings"); expect(s).to_be_visible()
    save_btn = page.get_by_test_id("notif-save"); expect(save_btn).to_be_disabled()
    # per-event toggle: in-app job_stopped off -> save -> reload persists
    ev = page.get_by_test_id("notif-ev-in_app-job_stopped"); clickable(ev)
    was = ev.is_checked(); ev.click(); expect(ev).to_be_checked(checked=not was)
    expect(save_btn).to_be_enabled(); save_btn.click(); toast(page, "saved")
    page.reload(); expect(page.get_by_test_id("notif-ev-in_app-job_stopped")).to_be_checked(checked=not was)
    assert api("/notifications/settings")["in_app"]["events"]["job_stopped"] == (not was)
    page.get_by_test_id("notif-ev-in_app-job_stopped").click(); page.get_by_test_id("notif-save").click(); toast(page, "saved")
    # webhook test to an unroutable local target -> clean error
    page.get_by_test_id("notif-webhook-url").fill("http://127.0.0.1:9/hook")
    page.get_by_test_id("notif-webhook-test").click()
    res = page.get_by_test_id("notif-webhook-result"); expect(res).to_be_visible(timeout=20_000)
    info.append(f"{MODE} webhook test result: {res.inner_text()[:140]!r}")
    assert re.search(r"fail|error|refused|could not|unreachable", res.inner_text(), re.I), res.inner_text()
    # SMTP: local unroutable target, password write-only
    page.get_by_test_id("notif-smtp-host").fill("127.0.0.1"); page.get_by_test_id("notif-smtp-port").fill("2525")
    page.get_by_test_id("notif-smtp-security").select_option("none") if page.locator("[data-testid=notif-smtp-security] option[value=none]").count() else None
    page.get_by_test_id("notif-smtp-user").fill("test_user"); page.get_by_test_id("notif-smtp-from").fill("r2a-test@example.invalid")
    page.get_by_test_id("notif-smtp-to").fill("r2a-test@example.invalid")
    pw = page.get_by_test_id("notif-smtp-password"); expect(pw).to_have_attribute("type", "password"); pw.fill(SMTP_PW)
    page.get_by_test_id("notif-email-test").click()
    er = page.get_by_test_id("notif-email-result"); expect(er).to_be_visible(timeout=30_000)
    info.append(f"{MODE} email test result: {er.inner_text()[:140]!r}")
    assert re.search(r"fail|error|refused|could not|connect", er.inner_text(), re.I), er.inner_text()
    responses = []
    page.on("response", lambda r: "/api/notifications" in r.url and responses.append(r))
    with page.expect_response(lambda r: r.url.endswith("/api/notifications/settings") and r.request.method == "PUT") as put:
        page.get_by_test_id("notif-save").click()
    assert put.value.status == 200, put.value.text()
    assert SMTP_PW not in put.value.text(), "PUT response echoes the SMTP password"
    toast(page, "saved")
    g = api("/notifications/settings"); assert g["email"]["password_set"] and SMTP_PW not in json.dumps(g), g["email"]
    expect(page.get_by_test_id("notif-smtp-password")).to_have_value("")
    assert SMTP_PW not in page.content(), "password in DOM after save"
    page.reload(); expect(page.get_by_test_id("notif-smtp-host")).to_have_value("127.0.0.1")
    expect(page.get_by_test_id("notif-smtp-password")).to_have_value("")
    ph = page.get_by_test_id("notif-smtp-password").get_attribute("placeholder"); info.append(f"{MODE} saved password placeholder: {ph!r}")
    assert SMTP_PW not in page.content() and SMTP_PW not in (ph or "")
    for r in responses:
        try: assert SMTP_PW not in r.text(), f"password echoed by {r.url}"
        except Exception as e:
            if "password echoed" in str(e): raise
    # clear the password + restore the other values through the UI
    page.get_by_test_id("notif-smtp-password-clear").click()
    page.get_by_test_id("notif-smtp-host").fill(before["email"]["host"]); page.get_by_test_id("notif-smtp-port").fill(str(before["email"]["port"]))
    page.get_by_test_id("notif-smtp-security").select_option(before["email"]["security"])
    page.get_by_test_id("notif-smtp-user").fill(before["email"]["username"]); page.get_by_test_id("notif-smtp-from").fill(before["email"]["from_addr"])
    page.get_by_test_id("notif-smtp-to").fill(", ".join(before["email"]["to_addrs"])); page.get_by_test_id("notif-webhook-url").fill(before["webhook"]["url"])
    page.get_by_test_id("notif-save").click(); toast(page, "saved")
    g = api("/notifications/settings")
    assert not g["email"]["password_set"] and g["email"]["host"] == before["email"]["host"] and g["webhook"]["url"] == before["webhook"]["url"], g
    # browser notifications toggle (permission granted in this context)
    bt = page.get_by_test_id("notif-browser-toggle"); expect(bt).to_be_visible(); info.append(f"{MODE} browser toggle enabled={bt.is_enabled()} perm={page.evaluate('typeof Notification!=="undefined" ? Notification.permission : "unsupported"')}")
    if bt.is_enabled():
        bt.click(); expect(bt).to_be_checked(); info.append(f"{MODE} browser status: {page.get_by_test_id('notif-browser-status').inner_text()[:80]!r}")
        bt.click(); expect(bt).not_to_be_checked()

def flow_git(page):
    open_detail(page, "R1")
    page.get_by_test_id("detail-tab-git").click()
    gp = page.get_by_test_id("git-panel"); expect(gp).to_be_visible()
    expect(page.get_by_test_id("git-commit-count")).to_have_text("2 commits")
    items = page.locator("[data-testid^=git-commit-]").filter(has=page.locator("svg"))
    msgs = page.locator("[data-testid=git-commit-list] li").all_inner_texts()
    assert len(msgs) == 2 and all(re.search(rf"Step {i}: test step {i}", " ".join(msgs)) for i in range(1, 3)), msgs
    page.locator("[data-testid=git-commit-list] li button").nth(1).click()   # Step 3 (newest first)
    expect(page.get_by_test_id("git-commit-subject")).to_contain_text("Step")
    dv = page.get_by_test_id("git-commit-diff"); expect(dv).to_be_visible()
    expect(dv.get_by_test_id("diff-stats")).to_be_visible()
    links = dv.get_by_test_id("diff-file-link"); assert links.count() >= 1
    fl = dv.get_by_test_id("diff-file-list").inner_text(); assert re.search(r"\+\d", fl), fl
    sp, un = dv.get_by_test_id("diff-mode-split"), dv.get_by_test_id("diff-mode-unified")
    if sp.is_visible():
        un.click(); expect(un).to_have_attribute("aria-pressed", "true"); expect(sp).to_have_attribute("aria-pressed", "false")
        sp.click(); expect(sp).to_have_attribute("aria-pressed", "true")
    else:
        info.append(f"{MODE}: split/unified toggle hidden")
    dv.get_by_test_id("diff-collapse-all").click(); expect(dv.get_by_test_id("diff-file-body")).to_have_count(0)
    dv.get_by_test_id("diff-expand-all").click(); expect(dv.get_by_test_id("diff-file-body").first).to_be_visible()
    links.first.click()
    page.get_by_test_id("git-compare-button").click()
    cmp = page.get_by_test_id("git-compare"); expect(cmp).to_be_visible()
    shas = page.get_by_test_id("git-compare-base").locator("option").all()
    expect(page.get_by_test_id("git-compare-diff")).to_be_visible()
    info.append(f"{MODE} compare default: {page.get_by_test_id('git-compare-diff').get_by_test_id('diff-stats').inner_text()[:80]!r}")
    head = page.get_by_test_id("git-compare-head").input_value()
    page.get_by_test_id("git-compare-base").select_option(head)
    expect(page.get_by_test_id("git-compare").get_by_text(re.compile("No differences|base and head|same", re.I)).first).to_be_visible()
    page.get_by_test_id("git-compare-close").click(); expect(cmp).to_have_count(0)
    name, size, p = dl(page, page.get_by_test_id("git-download-zip"))
    names = zipfile.ZipFile(p).namelist(); assert size > 0 and any(n.startswith(".git/") or "/.git/" in n for n in names), (name, size, names[:5])
    name2, size2, p2 = dl(page, page.get_by_test_id("git-download-bundle"))
    assert size2 > 0 and open(p2, "rb").read(20).startswith(b"# v"), (name2, size2)
    info.append(f"{MODE} repo zip {name} {size} B, bundle {name2} {size2} B")
    oct_ = page.locator("svg.octicon").count(); info.append(f"{MODE} octicons on git tab: {oct_}"); assert oct_ > 3

def flow_github_settings(page, fake):
    page.goto(BASE + "/?tab=settings"); g = page.get_by_test_id("github-settings"); expect(g).to_be_visible()
    expect(page.get_by_test_id("gh-oauth-unconfigured")).to_be_visible()
    inp, btn = page.get_by_test_id("gh-token-input"), page.get_by_test_id("gh-connect")
    expect(inp).to_have_attribute("type", "password"); expect(btn).to_be_disabled()
    inp.fill("short"); btn.click()                      # real backend 422 (no GitHub call)
    expect(page.get_by_test_id("gh-error")).to_contain_text("does not look like a GitHub token")
    inp.fill(FAKE_TOKEN); btn.click()                   # intercepted -> stubbed 401
    expect(page.get_by_test_id("gh-error")).to_contain_text("401")
    html = page.content(); kept = inp.input_value() == FAKE_TOKEN
    info.append(f"{MODE} after 401: token kept in input={kept}, occurrences in DOM={html.count(FAKE_TOKEN)}")
    assert FAKE_TOKEN not in html.replace(f'value="{FAKE_TOKEN}"', ""), "fake token shown in the DOM outside the input after 401"
    assert api("/github")["connected"] is False, "backend says connected"
    # stubbed connected state: PAT -> OAuth card disabled
    fake.stub = True; fake.accept = True
    inp.fill(FAKE_TOKEN); btn.click()
    expect(page.get_by_test_id("gh-username")).to_have_text("@r2a-fake-tester")
    expect(page.get_by_test_id("gh-pat-active")).to_be_visible()
    expect(page.get_by_test_id("gh-oauth-disabled-note")).to_be_visible()
    expect(page.get_by_test_id("gh-oauth-start")).to_have_count(0); expect(page.get_by_test_id("gh-token-input")).to_have_count(0)
    assert FAKE_TOKEN not in page.content(), "fake token in DOM after (stubbed) connect"
    ap = page.get_by_test_id("gh-autopush-enabled"); expect(page.get_by_test_id("gh-autopush-private")).to_be_disabled()
    ap.click(); expect(ap).to_be_checked(); expect(page.get_by_test_id("gh-autopush-private")).to_be_enabled(); ap.click(); expect(ap).not_to_be_checked()
    d = page.get_by_test_id("gh-disconnect"); d.click(); expect(d).to_have_attribute("data-armed", "true"); d.click()
    expect(page.get_by_test_id("gh-token-input")).to_be_visible()
    # OAuth: save client id -> start -> pending disables PAT -> cancel
    page.get_by_test_id("gh-oauth-client-id").fill("Iv1.testfake0000"); page.get_by_test_id("gh-oauth-client-id-save").click()
    page.get_by_test_id("gh-oauth-start").click()
    expect(page.get_by_test_id("gh-oauth-code")).to_have_text("TEST-0000")
    expect(page.get_by_test_id("gh-pat-disabled-note")).to_be_visible()
    expect(page.get_by_test_id("gh-token-input")).to_be_disabled(); expect(page.get_by_test_id("gh-connect")).to_be_disabled()
    page.get_by_test_id("gh-oauth-cancel").click()
    expect(page.get_by_test_id("gh-oauth-pending")).to_have_count(0); expect(page.get_by_test_id("gh-token-input")).to_be_enabled()
    real = api("/github"); b = json.load(open(f"{DIR}/before_github.json"))
    assert {k: real[k] for k in ("connected", "auth_method", "auto_push", "oauth")} == {k: b[k] for k in ("connected", "auth_method", "auto_push", "oauth")}, "real GitHub settings changed!"

def flow_push_dialog(page, fake):
    open_detail(page, "R1")
    page.get_by_test_id("push-github-button").click()
    sh = page.get_by_test_id("push-github-sheet"); expect(sh).to_be_visible()
    expect(page.get_by_test_id("gh-not-connected")).to_be_visible()      # real state
    expect(page.get_by_test_id("gh-open-settings")).to_be_visible()
    page.keyboard.press("Escape"); expect(sh).to_be_hidden()
    fake.stub = True; fake.method = "pat"
    page.get_by_test_id("push-github-button").click(); expect(sh).to_be_visible()
    push = page.get_by_test_id("gh-push")
    expect(page.get_by_test_id("gh-repo-name")).not_to_have_value(""); expect(page.get_by_test_id("gh-branch")).to_have_value(re.compile(r"^r2a/job-"))
    expect(page.get_by_test_id("gh-open-pr")).to_be_disabled(); expect(page.get_by_test_id("gh-pr-new-hint")).to_be_visible()
    page.get_by_test_id("gh-repo-name").fill(""); expect(push).to_be_disabled()
    page.get_by_test_id("gh-repo-name").fill(f"test_{SUF}-repo"); expect(push).to_be_enabled()
    page.get_by_test_id("gh-branch").fill("  "); expect(push).to_be_disabled()
    page.get_by_test_id("gh-branch").fill("@{-1}"); push.click()
    expect(page.get_by_test_id("gh-push-error")).to_contain_text("not a valid branch name")
    page.get_by_test_id("gh-mode-existing").click()
    expect(page.get_by_test_id("gh-repo-list")).to_be_visible(); expect(push).to_be_disabled()
    expect(page.get_by_test_id("gh-repo-someone/read-only")).to_be_disabled()
    page.get_by_test_id("gh-repo-r2a-fake-tester/test-repo").click()
    page.get_by_test_id("gh-open-pr").check(); expect(page.get_by_test_id("gh-pr-title")).not_to_have_value("")
    expect(page.get_by_test_id("gh-pr-base")).to_have_attribute("placeholder", "main")
    expect(push).to_have_text(re.compile("Push and open PR"))
    page.get_by_test_id("gh-branch").fill(f"test_{SUF}/branch"); clickable(push); push.click()   # aborted by the route
    expect(page.get_by_test_id("gh-push-error")).to_be_visible()
    info.append(f"{MODE} aborted push shows: {page.get_by_test_id('gh-push-error').inner_text()[:120]!r}")
    assert page.locator("svg.octicon").count() > 3

def flow_a11y_icons(page, fake):
    found = {}
    def scan(where):
        for x in unnamed_buttons(page): found.setdefault(x, where)
    for t, ready in (("create", "create-job-tab"), ("queue", "queue-tab"), ("current", "current-empty"), ("history", "history-panel"), ("settings", "settings-tab")):
        page.goto(f"{BASE}/?tab={t}"); expect(page.get_by_test_id(ready)).to_be_visible(); page.wait_for_load_state("networkidle"); scan(t)
    open_detail(page, "R1")
    for t in ("transcript", "files", "steps", "git", "log"):
        page.get_by_test_id(f"detail-tab-{t}").click(); page.wait_for_timeout(300); scan(f"detail/{t}")
    page.get_by_test_id("detail-tab-git").click(); page.locator("[data-testid=git-commit-list] li button").first.click()
    expect(page.get_by_test_id("git-commit-diff")).to_be_visible(); scan("detail/git commit")
    page.get_by_test_id("notif-bell").click(); expect(page.get_by_test_id("notif-panel")).to_be_visible(); scan("notifications"); page.keyboard.press("Escape")
    page.get_by_test_id("clone-job-button").click(); expect(page.get_by_test_id("clone-job-sheet")).to_be_visible(); scan("clone sheet"); page.keyboard.press("Escape")
    page.goto(f"{BASE}/?tab=history"); expect(page.get_by_test_id("history-panel")).to_be_visible()
    if not MOBILE:
        page.get_by_test_id("history-panel-collapse").click(); expect(page.get_by_test_id("history-panel-rail")).to_be_visible(); scan("history rail")
        page.get_by_test_id("history-panel-expand").click()
    info.append(f"{MODE} unnamed icon buttons: {found or 'none'}")
    assert not found, f"buttons without accessible name: {found}"

def flow_delete(page):
    """single delete (S1 from detail), bulk select delete (E1 + Q2), delete-all-finished (own finished only)."""
    open_detail(page, "S1")
    b = page.get_by_test_id("delete-job-button"); b.click(); expect(b).to_have_attribute("data-armed", "true")
    page.locator("body").click(position={"x": 5, "y": 300}); expect(b).to_have_attribute("data-armed", "false")   # blur disarms
    b.click(); b.click(); toast(page, "Deleted")
    expect(page.get_by_test_id(f"history-job-{J('S1')}")).to_have_count(0)
    page.goto(BASE + "/?tab=history"); page.get_by_test_id("history-select-toggle").click()
    for k in ("E1", "Q2"): page.get_by_test_id(f"history-job-{J(k)}").click()
    expect(page.get_by_test_id("history-selected-count")).to_have_text("2 selected")
    bd = page.get_by_test_id("history-bulk-delete"); clickable(bd); bd.click(); expect(bd).to_contain_text("Delete 2 jobs?"); bd.click()
    toast(page, "Deleted 2 jobs")
    for k in ("E1", "Q2"): expect(page.get_by_test_id(f"history-job-{J(k)}")).to_have_count(0)
    pre = {j["job_id"] for j in api("/jobs?limit=200")} - my_ids()
    df = page.get_by_test_id("history-delete-finished"); df.click(); expect(df).to_have_attribute("data-armed", "true"); df.click()
    toast(page, "Deleted")
    left = {j["job_id"] for j in api("/jobs?limit=200")}
    assert pre <= left, "pre-existing jobs were deleted!"
    assert not (left & my_ids()), f"own jobs left: {left & my_ids()}"

# ------------------------------------------------------------------ regression
def flow_reg_queue_pickup(page):
    wait_idle()
    page.goto(BASE + "/?tab=create"); expect(page.get_by_test_id("model-input")).not_to_have_value("")
    k = f"{MODE[0]}"
    assert create_job(page, f"A{k}", "stub-slow", 2)["status"] == "running"
    for x in ("B", "C", "D"): create_job(page, f"{x}{k}", "gpt-4o", 2)
    tab(page, "queue"); expect(rows(page)).to_have_count(3)
    assert order(page) == [J(f"{x}{k}") for x in "BCD"]
    row(page, f"C{k}").get_by_test_id("queue-move-up").click()
    expect(rows(page).first).to_have_attribute("data-testid", f"queue-row-{J('C'+k)}")
    row(page, f"C{k}").get_by_test_id("queue-move-down").click()
    expect(rows(page).first).to_have_attribute("data-testid", f"queue-row-{J('B'+k)}")
    row(page, f"B{k}").get_by_test_id("queue-pause").click()
    expect(row(page, f"B{k}").get_by_test_id("queue-row-status")).to_have_text(re.compile("Paused", re.I))
    expect(rows(page).last).to_have_attribute("data-testid", f"queue-row-{J('B'+k)}")
    row(page, f"B{k}").get_by_test_id("queue-unpause").click()
    expect(row(page, f"B{k}").get_by_test_id("queue-row-status")).to_have_text(re.compile("Queued", re.I))
    row(page, f"D{k}").get_by_test_id("queue-remove").click(); row(page, f"D{k}").get_by_test_id("queue-remove-yes").click()
    expect(row(page, f"D{k}")).to_have_count(0)
    page.goto(BASE + "/?tab=current")
    titles, end = [], time.time() + 80
    while time.time() < end:
        if page.get_by_test_id("current-empty-title").is_visible():
            q = api("/queue")
            if not q["running"] and not q["waiting"]: break
        t = page.get_by_test_id("job-view-title")
        if t.count():
            try: txt = t.inner_text(timeout=800)
            except Exception: txt = None
            if txt and (not titles or titles[-1] != txt): titles.append(txt)
        page.wait_for_timeout(300)
    expect(page.get_by_test_id("current-empty-title")).to_have_text("Waiting for next job")
    seen = [t.split()[-1] for t in titles]; info.append(f"{MODE} current tab showed {seen}")
    assert seen and seen[0] == f"A{k}" and {f"B{k}", f"C{k}"} <= set(seen), titles

def flow_reg_history(page):
    k = MODE[0]
    page.goto(BASE + "/?tab=history"); page.evaluate("localStorage.removeItem('r2a.historyPanel.collapsed')"); page.reload()
    page.get_by_test_id("history-panel-collapse").click()
    if MOBILE:
        expect(page.get_by_test_id("history-list")).to_have_count(0); page.reload(); expect(page.get_by_test_id("history-list")).to_have_count(0)
        page.get_by_test_id("history-panel-collapse").click(); expect(page.get_by_test_id("history-list")).to_be_visible()
    else:
        expect(page.get_by_test_id("history-panel-rail")).to_be_visible(); page.reload(); expect(page.get_by_test_id("history-panel-rail")).to_be_visible()
        page.get_by_test_id("history-panel-expand").click()
    page.reload(); expect(page.get_by_test_id("history-panel")).to_be_visible()
    jid = J(f"C{k}")
    page.goto(f"{BASE}/?tab=history&job={jid}"); expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"C{k}")
    page.reload(); expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"C{k}")
    if MOBILE:
        page.get_by_test_id("history-back-to-list").click(); expect(page.get_by_test_id("history-list")).to_be_visible()
        page.get_by_test_id(f"history-job-{jid}").click(); expect(page.get_by_test_id("history-detail")).to_be_visible()
    # downloads
    page.get_by_test_id("detail-tab-transcript").click(); expect(page.get_by_test_id("full-transcript")).to_be_visible()
    n, s, p = dl(page, page.get_by_test_id("export-transcript-button")); assert s > 500 and "<script" not in open(p).read().lower()
    page.get_by_test_id("detail-tab-files").click(); page.get_by_test_id("file-tree-item-app/models.py").click()
    expect(page.get_by_test_id("code-pane")).to_contain_text("class Item")
    n2, s2, _ = dl(page, page.get_by_test_id("file-download-button")); assert s2 > 0
    zb = page.locator("[data-testid=download-zip-button]:visible").first
    n3, s3, p3 = dl(page, zb); assert s3 > 0 and "app/main.py" in zipfile.ZipFile(p3).namelist()
    info.append(f"{MODE} downloads: {n} {s}B, {n2} {s2}B, {n3} {s3}B")

def flow_reg_clone(page):
    k = MODE[0]; wait_idle()
    page.goto(BASE + "/?tab=create"); expect(page.get_by_test_id("model-input")).not_to_have_value("")
    create_job(page, f"H{k}", "stub-slow", 2)
    src = J(f"C{k}"); page.goto(f"{BASE}/?tab=history&job={src}")
    page.get_by_test_id("clone-job-button").click(); sh = page.get_by_test_id("clone-job-sheet"); expect(sh).to_be_visible()
    s = api(f"/jobs/{src}")
    expect(sh.get_by_test_id("roadmap-input")).to_have_value(s["roadmap_md"]); expect(sh.get_by_test_id("model-input")).to_have_value(s["model"])
    title = f"test_{SUF} K{k} clone"
    sh.get_by_test_id("roadmap-input").fill(s["roadmap_md"].replace(f"# test_{SUF} C{k}", f"# {title}", 1))
    sh.get_by_test_id("project-context-input").fill("test clone ctx")
    expect(sh.get_by_test_id("clone-edited-note")).to_be_visible()
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
        sh.get_by_test_id("start-job-button").click()
    d = resp.value.json(); track(d["job_id"], f"K{k}"); assert d["status"] == "queued", d
    expect(page.get_by_test_id("history-detail-title")).to_have_text(title)
    tab(page, "queue"); expect(page.get_by_test_id(f"queue-row-{d['job_id']}")).to_contain_text(title)
    assert api(f"/jobs/{d['job_id']}")["cloned_from"] == src

def flow_reg_settings(page):
    before = api("/settings")
    page.goto(BASE + "/?tab=settings"); expect(page.get_by_test_id("setting-model-input")).to_have_value(before["model"])
    page.get_by_test_id("setting-step_delay_seconds-input").fill("700"); expect(page.get_by_test_id("setting-step_delay_seconds-error")).to_be_visible()
    page.get_by_test_id("setting-model-input").fill(f"test_{SUF}-model"); page.get_by_test_id("setting-step_delay_seconds-input").fill("1")
    page.get_by_test_id("settings-save").click(); toast(page, "Settings saved")
    page.reload(); expect(page.get_by_test_id("setting-model-input")).to_have_value(f"test_{SUF}-model")
    page.get_by_test_id("settings-reset").click(); page.get_by_test_id("settings-reset-yes").click()
    expect(page.get_by_test_id("setting-model-input")).to_have_value(before["env_defaults"]["model"])
    page.reload(); expect(page.get_by_test_id("setting-step_delay_seconds-input")).to_have_value(str(before["env_defaults"]["step_delay_seconds"]))

def flow_reg_tabs(page):
    page.goto(BASE + "/"); expect(page).to_have_url(re.compile(r"tab=create"))
    for t, ready in (("queue", "queue-tab"), ("current", "current-empty"), ("history", "history-panel"), ("settings", "settings-tab"), ("create", "create-job-tab")):
        loc = page.get_by_test_id(f"tab-{t}"); clickable(loc); loc.click(); expect(page.get_by_test_id(ready)).to_be_visible()
        if overflow(page) > 1: raise AssertionError(f"tab {t} overflows by {overflow(page)}px")
    page.goto(BASE + "/no/such/route"); expect(page.get_by_test_id("main-tabs")).to_be_visible()
    page.goto(BASE + "/?tab=bogus"); expect(page.get_by_test_id("create-job-tab")).to_be_visible()

NEEDS_FAKE = {"github_settings", "push_dialog", "a11y_icons"}
if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for name in sys.argv[2:]:
            ctx = b.new_context(viewport=VIEWPORTS[MODE], accept_downloads=True, is_mobile=MOBILE, has_touch=MOBILE)
            ctx.grant_permissions(["notifications"], origin=BASE)
            fake = FakeGH(); install_routes(ctx, fake)
            page = ctx.new_page(); instrument(page)
            try:
                fn = globals()["flow_" + name]
                fn(page, fake) if name in NEEDS_FAKE else fn(page)
                print(f"PASS {MODE}:{name}")
            except Exception as e:
                page.screenshot(path=f"{SHOTS}/{MODE}_{name}.png", full_page=True)
                print(f"FAIL {MODE}:{name}: {str(e)[:900]}")
            ctx.close()
        b.close()
    for i in info: print("   info:", i)
    print("GITHUB OUT (aborted):", state["github_out"] or None, "| PUSH ATTEMPTS (stubbed/aborted):", len(state["pushes"]))
    print("CONSOLE:", dict(console) or None); print("REQUESTFAILED:", dict(netfail) or None)
    print("API>=400:", dict(api_bad) or None); print("WRONG HOST/PREFIX:", dict(wrong) or None)
