"""ROADMAP2ARENA e2e (tabs, queue, current job, history panel, detail, clone, settings).
Usage: PYTHONDONTWRITEBYTECODE=1 python e2e_history.py <desktop|mobile> flow [flow...]
State lives in /tmp (files under /app/frontend trigger Vite full reloads)."""
import json, os, random, re, string, sys, time, zipfile, urllib.request
from collections import Counter
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8080"
STATE = "/tmp/r2a3_state.json"
SHOTS = "/tmp/frontend-test"
IGNORE = re.compile(r"\[vite\]|React DevTools|favicon\.ico", re.I)
VIEWPORTS = {"desktop": {"width": 1280, "height": 800}, "mobile": {"width": 390, "height": 844}}

state = json.load(open(STATE)) if os.path.exists(STATE) else {"jobs": {}, "suffix": "".join(random.choices(string.ascii_lowercase + string.digits, k=5))}
def save(): json.dump(state, open(STATE, "w"), indent=1)
SUF = state["suffix"]; save()
MODE = sys.argv[1]
console, netfail, api_bad, wrong = Counter(), Counter(), Counter(), Counter()
info = []

def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + "/api" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def instrument(page):
    page.set_default_timeout(10_000)
    page.on("console", lambda m: m.type in ("error", "warning") and not IGNORE.search(m.text) and console.update([f"{m.type}: {m.text[:180]}"]))
    page.on("pageerror", lambda e: console.update([f"pageerror: {str(e)[:180]}"]))
    page.on("requestfailed", lambda r: not IGNORE.search(r.url) and netfail.update([f"{r.method} {re.sub(r'[0-9a-f-]{36}', '<id>', r.url)} {r.failure}"]))
    def onresp(r):
        if "/api/" in r.url and r.status >= 400:
            api_bad.update([f"{r.request.method} {re.sub(r'[0-9a-f-]{36}', '<id>', r.url.split('?')[0])} -> {r.status}"])
    page.on("response", onresp)
    def onreq(r):
        u = r.url
        if u.startswith(("data:", "blob:")) or "fonts.g" in u: return
        if not u.startswith(BASE): wrong.update([u[:120]])
        elif r.resource_type in ("fetch", "xhr") and "/api/" not in u: wrong.update(["no /api prefix: " + u[:120]])
    page.on("request", onreq)
    page.on("framenavigated", lambda f: f == page.main_frame and os.environ.get("DBG") and print("   nav", f.url))

def roadmap(n, title):
    return f"# {title}\n\n" + "\n".join(f"### test step {i}\nCreate file {i}.\n" for i in range(1, n + 1))

def tab(page, name):
    page.get_by_test_id(f"tab-{name}").click()
    expect(page).to_have_url(re.compile(rf"tab={name}"))

def create_job(page, key, model, n, extra=""):
    title = f"test_{SUF} {key}{extra}"
    tab(page, "create")
    page.get_by_test_id("model-input").fill(model)
    page.get_by_test_id("roadmap-input").fill(roadmap(n, title))
    expect(page.get_by_test_id("steps-found")).to_have_text(f"{n} step{'s' if n > 1 else ''} found")
    btn = page.get_by_test_id("start-job-button")
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
        btn.click()
    assert resp.value.status == 201, f"create {key}: {resp.value.status} {resp.value.text()[:200]}"
    d = resp.value.json()
    state["jobs"][key] = d["job_id"]; state.setdefault("all", []).append(d["job_id"]); save()
    return d

def job_status(key):
    return api(f"/jobs/{state['jobs'][key]}")["status"]

def wait_status(key, statuses, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        s = job_status(key)
        if s in statuses: return s
        time.sleep(0.5)  # API polling (not UI)
    raise AssertionError(f"{key} never reached {statuses} (last {s})")

def wait_idle(timeout=100):
    end = time.time() + timeout
    while time.time() < end:
        q = api("/queue")
        if not q["running"] and not q["waiting"]: return
        time.sleep(1)
    raise AssertionError("queue never went idle")

def clickable(loc):
    loc.scroll_into_view_if_needed(); loc.click(trial=True)

def row(page, key): return page.get_by_test_id(f"queue-row-{state['jobs'][key]}")

def queue_order(page):
    return [el.get_attribute("data-testid")[10:] for el in page.locator("li[data-testid^=queue-row-]").all()]

def ids(*keys): return [state["jobs"][k] for k in keys]

# ------------------------------------------------------------------ flows
def flow_tabs(page):
    page.goto(BASE + "/")
    expect(page).to_have_url(re.compile(r"\?tab=create"))
    expect(page.get_by_test_id("create-job-tab")).to_be_visible()
    expect(page.get_by_test_id("model-input")).not_to_have_value("")
    for t, tid in (("queue", "queue-tab"), ("current", "current-empty"), ("history", "history-panel"), ("settings", "settings-tab"), ("create", "create-job-tab")):
        clickable(page.get_by_test_id(f"tab-{t}")); tab(page, t)
        expect(page.get_by_test_id(tid)).to_be_visible()
    tab(page, "current"); expect(page.get_by_test_id("current-empty-title")).to_have_text("Waiting for next job")
    tab(page, "queue"); expect(page.get_by_test_id("queue-empty")).to_be_visible(); expect(page.get_by_test_id("queue-idle")).to_be_visible()
    page.reload(); expect(page.get_by_test_id("queue-tab")).to_be_visible()  # tab survives reload
    page.go_back() if False else None
    page.goto(BASE + "/no/such/route"); expect(page.get_by_test_id("main-tabs")).to_be_visible()
    page.goto(BASE + "/?tab=bogus"); expect(page.get_by_test_id("create-job-tab")).to_be_visible()

def flow_create_validation(page):
    page.goto(BASE + "/?tab=create")
    expect(page.get_by_test_id("model-input")).not_to_have_value("")
    page.get_by_test_id("roadmap-input").fill(roadmap(2, f"test_{SUF} val"))
    expect(page.get_by_test_id("steps-found")).to_have_text("2 steps found")
    page.get_by_test_id("model-input").fill("  ")
    expect(page.get_by_test_id("model-error")).to_have_text("Model is required")
    expect(page.get_by_test_id("start-job-button")).to_be_disabled()
    page.get_by_test_id("model-input").fill("gpt-4o"); page.get_by_test_id("arena-url-input").fill("ftp://x")
    expect(page.get_by_test_id("arena_url-error")).to_contain_text("http://")
    expect(page.get_by_test_id("start-job-button")).to_be_disabled()
    page.get_by_test_id("arena-url-input").fill("http://localhost:9090")
    expect(page.get_by_test_id("start-job-button")).to_be_enabled()
    page.get_by_test_id("roadmap-input").fill("no steps here")
    expect(page.get_by_test_id("steps-found")).to_have_text("No steps found")
    expect(page.get_by_test_id("start-job-button")).to_be_disabled()
    expect(page.get_by_test_id("queue-hint")).to_contain_text("starts right away")

def flow_queue(page):
    """Long running A, queued B, C, D: order, move, pause/unpause, remove (cancelled), badges."""
    wait_idle()
    page.goto(BASE + "/?tab=create")
    expect(page.get_by_test_id("model-input")).not_to_have_value("")
    a = create_job(page, "A", "stub-slow", 5)
    assert a["status"] == "running", a
    expect(page).to_have_url(re.compile("tab=current"))
    expect(page.get_by_test_id("job-view-status")).to_have_text(re.compile("Running", re.I))
    tab(page, "create"); expect(page.get_by_test_id("queue-hint")).to_contain_text("1 job ahead")
    for k in ("B", "C", "D"):
        d = create_job(page, k, "gpt-4o", 3)
        assert d["status"] == "queued", d
    tab(page, "queue")
    expect(page.get_by_test_id("queue-running-title")).to_contain_text(f"test_{SUF} A")
    expect(page.locator("li[data-testid^=queue-row-]")).to_have_count(3)
    assert queue_order(page) == ids("B", "C", "D"), queue_order(page)
    expect(row(page, "B").get_by_test_id("queue-position")).to_have_text("1")
    expect(row(page, "B").get_by_test_id("queue-move-up")).to_be_disabled()
    expect(row(page, "D").get_by_test_id("queue-move-down")).to_be_disabled()
    expect(page.get_by_test_id("queue-count-badge")).to_have_text("3")
    row(page, "C").get_by_test_id("queue-move-up").click()
    expect(page.locator("li[data-testid^=queue-row-]").first).to_have_attribute("data-testid", f"queue-row-{state['jobs']['C']}")
    assert queue_order(page) == ids("C", "B", "D"), queue_order(page)
    row(page, "C").get_by_test_id("queue-move-down").click()
    expect(page.locator("li[data-testid^=queue-row-]").first).to_have_attribute("data-testid", f"queue-row-{state['jobs']['B']}")
    assert queue_order(page) == ids("B", "C", "D"), queue_order(page)
    page.reload(); expect(page.locator("li[data-testid^=queue-row-]")).to_have_count(3)
    assert queue_order(page) == ids("B", "C", "D"), "order after reload " + str(queue_order(page))
    # pause B -> moves to end, Paused badge
    row(page, "B").get_by_test_id("queue-pause").click()
    expect(row(page, "B").get_by_test_id("queue-row-status")).to_have_text(re.compile("Paused", re.I))
    expect(page.locator("li[data-testid^=queue-row-]").last).to_have_attribute("data-testid", f"queue-row-{state['jobs']['B']}")
    expect(page.get_by_test_id("queue-summary")).to_contain_text("1 paused")
    # history shows queued / paused / running badges now
    tab(page, "history")
    expect(page.get_by_test_id(f"history-job-{state['jobs']['B']}").get_by_test_id("history-job-status")).to_have_text(re.compile("Paused", re.I))
    expect(page.get_by_test_id(f"history-job-{state['jobs']['C']}").get_by_test_id("history-job-status")).to_have_text(re.compile("Queued", re.I))
    expect(page.get_by_test_id(f"history-job-{state['jobs']['A']}").get_by_test_id("history-job-status")).to_have_text(re.compile("Running", re.I))
    tab(page, "queue")
    row(page, "B").get_by_test_id("queue-unpause").click()
    expect(row(page, "B").get_by_test_id("queue-row-status")).to_have_text(re.compile("Queued", re.I))
    # remove D (two-click confirm)
    row(page, "D").get_by_test_id("queue-remove").click()
    expect(row(page, "D").get_by_test_id("queue-remove-confirm")).to_be_visible()
    row(page, "D").get_by_test_id("queue-remove-yes").click()
    expect(row(page, "D")).to_have_count(0)
    assert job_status("D") == "cancelled", job_status("D")
    order = queue_order(page)
    info.append(f"queue order after pause/unpause/remove: {[k for i in order for k, v in state['jobs'].items() if v == i]}")
    st = job_status("A"); assert st == "running", f"A finished too early ({st}); queue ops took too long"

def flow_pickup(page):
    """Current job: follows A -> next queued -> ... -> 'Waiting for next job'."""
    page.goto(BASE + "/?tab=current")
    titles, waits, end = [], 0, time.time() + 100
    while time.time() < end:
        if page.get_by_test_id("current-empty-title").is_visible():
            waits += 1
            q = api("/queue")
            if not q["running"] and not q["waiting"]:
                break
        else:
            t = page.get_by_test_id("job-view-title")
            if t.count():
                try: txt = t.inner_text(timeout=1000)
                except Exception: txt = None
                if txt and (not titles or titles[-1] != txt): titles.append(txt)
        page.wait_for_timeout(300)  # sampling interval
    info.append(f"current tab showed: {titles}; waiting-state samples: {waits}")
    expect(page.get_by_test_id("current-empty-title")).to_have_text("Waiting for next job")
    seen = [t.split()[-1] for t in titles]
    assert seen[:1] == ["A"] and set(seen) >= {"A", "B", "C"}, f"current tab did not pick up queued jobs automatically: {titles}"
    for k in ("A", "B", "C"): assert job_status(k) == "done", (k, job_status(k))
    expect(page.get_by_test_id("current-recent-job")).to_be_visible()

def flow_stop_error(page):
    """Produce Stopped (stop via Current job) and Failed (stub-503-at-3)."""
    wait_idle()
    page.goto(BASE + "/?tab=create")
    expect(page.get_by_test_id("model-input")).not_to_have_value("")
    create_job(page, "S", "stub-slow", 3)
    expect(page.get_by_test_id("job-view-status")).to_have_text(re.compile("Running", re.I))
    page.get_by_test_id("stop-job-button").click()
    page.get_by_test_id("confirm-stop-button").click()
    wait_status("S", ["stopped"], 20)
    # current tab resets after the stop
    expect(page.get_by_test_id("current-empty-title")).to_have_text("Waiting for next job", timeout=15_000)
    create_job(page, "E", "stub-503-at-3", 4)
    wait_status("E", ["error"], 60)
    page.goto(f"{BASE}/?tab=history&job={state['jobs']['E']}")
    expect(page.get_by_test_id("history-detail-status")).to_have_text(re.compile("Failed", re.I))
    expect(page.get_by_test_id("job-error-alert")).to_contain_text("Step 3")
    page.get_by_test_id("detail-tab-steps").click()
    for i, s in ((2, "done"), (3, "error"), (4, "pending")):
        expect(page.get_by_test_id(f"step-item-{i}")).to_have_attribute("data-status", s)

def flow_badges(page):
    """Every status present in history list badges + filter chips."""
    page.goto(BASE + "/?tab=history")
    expect(page.get_by_test_id("history-panel")).to_be_visible()
    want = {"A": "Completed", "S": "Stopped", "D": "Cancelled", "E": "Failed"}
    for k, label in want.items():
        expect(page.get_by_test_id(f"history-job-{state['jobs'][k]}").get_by_test_id("history-job-status")).to_have_text(re.compile(label, re.I))
    for f in ("queued", "paused", "running", "done", "error", "stopped", "cancelled"):
        expect(page.get_by_test_id(f"history-filter-{f}")).to_be_visible()
    page.get_by_test_id("history-filter-cancelled").click()
    expect(page.get_by_test_id(f"history-job-{state['jobs']['D']}")).to_be_visible()
    expect(page.get_by_test_id(f"history-job-{state['jobs']['A']}")).to_have_count(0)
    page.get_by_test_id("history-filter-all").click()
    page.get_by_test_id("history-search").fill(f"test_{SUF} E")
    expect(page.get_by_test_id(f"history-job-{state['jobs']['E']}")).to_be_visible()
    expect(page.get_by_test_id(f"history-job-{state['jobs']['A']}")).to_have_count(0)

def flow_panel_collapse(page):
    page.goto(BASE + "/?tab=history")
    page.evaluate("localStorage.removeItem('r2a.historyPanel.collapsed')"); page.reload()
    expect(page.get_by_test_id("history-panel")).to_be_visible()
    page.get_by_test_id("history-panel-collapse").click()
    if MODE == "desktop":
        expect(page.get_by_test_id("history-panel-rail")).to_be_visible()
        expect(page.get_by_test_id("history-panel")).to_have_count(0)
        page.reload(); expect(page.get_by_test_id("history-panel-rail")).to_be_visible()
        page.get_by_test_id(f"history-rail-job-{state['jobs']['A']}").click()
        expect(page).to_have_url(re.compile(f"job={state['jobs']['A']}"))
        expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"test_{SUF} A")
        page.get_by_test_id("history-panel-expand").click()
        expect(page.get_by_test_id("history-panel")).to_be_visible()
    else:
        expect(page.get_by_test_id("history-list")).to_have_count(0)
        page.reload(); expect(page.get_by_test_id("history-list")).to_have_count(0)
        page.get_by_test_id("history-panel-collapse").click()
        expect(page.get_by_test_id("history-list")).to_be_visible()
    page.reload(); expect(page.get_by_test_id("history-panel")).to_be_visible()
    assert page.evaluate("localStorage.getItem('r2a.historyPanel.collapsed')") == "0"

def flow_deep_link(page):
    jid = state["jobs"]["C"]
    page.goto(f"{BASE}/?tab=history&job={jid}")
    expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"test_{SUF} C")
    expect(page.get_by_test_id("history-detail-status")).to_have_text(re.compile("Completed", re.I))
    page.reload()
    expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"test_{SUF} C")
    expect(page).to_have_url(re.compile(f"tab=history&job={jid}"))
    if MODE == "mobile":
        expect(page.get_by_test_id("history-back-to-list")).to_be_visible()
        expect(page.get_by_test_id("history-list")).to_have_count(0)
        page.get_by_test_id("history-back-to-list").click()
        expect(page.get_by_test_id("history-list")).to_be_visible()
        expect(page).not_to_have_url(re.compile("job="))
        page.get_by_test_id(f"history-job-{jid}").click()
        expect(page.get_by_test_id("history-detail-title")).to_be_visible()
        page.go_back(); expect(page.get_by_test_id("history-list")).to_be_visible()
    else:
        expect(page.get_by_test_id(f"history-job-{jid}")).to_have_attribute("aria-current", "true")
    for bad in ("not-a-real-id", "00000000-0000-4000-8000-000000000000"):
        page.goto(f"{BASE}/?tab=history&job={bad}")
        expect(page.get_by_text(re.compile(r"Could not load job"))).to_be_visible()
        expect(page.get_by_test_id("main-tabs")).to_be_visible()
        page.reload(); expect(page.get_by_text(re.compile(r"Could not load job"))).to_be_visible()

def _dl(page, btn, timeout=20_000):
    with page.expect_download(timeout=timeout) as d:
        btn.click()
    p = d.value.path(); return d.value.suggested_filename, os.path.getsize(p), p

def flow_detail(page):
    jid = state["jobs"]["C"]
    page.goto(f"{BASE}/?tab=history&job={jid}")
    expect(page.get_by_test_id("history-detail-title")).to_contain_text(f"test_{SUF} C")
    page.get_by_test_id("detail-tab-transcript").click()
    expect(page.get_by_test_id("full-transcript")).to_be_visible()
    expect(page.locator("[data-testid^=transcript-turn-]")).to_have_count(3)
    expect(page.get_by_test_id("transcript-turn-1").get_by_test_id("turn-user")).to_be_visible()
    expect(page.get_by_test_id("transcript-turn-1").get_by_test_id("turn-assistant")).to_contain_text("app/main.py")
    name, size, p = _dl(page, page.get_by_test_id("export-transcript-button"))
    html = open(p, encoding="utf-8").read()
    assert name.endswith("-transcript.html") and size > 500 and "<script" not in html.lower() and "test step 1" in html, (name, size)
    info.append(f"{MODE}: transcript export {name} {size} B")
    page.get_by_test_id("detail-tab-files").click()
    expect(page.get_by_test_id("artifacts-panel")).to_be_visible()
    page.get_by_test_id("file-tree-item-app/models.py").click()
    expect(page.get_by_test_id("code-pane-path")).to_have_text("app/models.py")
    expect(page.get_by_test_id("code-pane")).to_contain_text("class Item")
    name, size, p = _dl(page, page.get_by_test_id("file-download-button"))
    body = open(p).read()
    assert name == "models.py" and size > 0 and "class Item" in body, (name, size, body[:80])
    page.get_by_test_id("file-tree-item-../evil.py").click()
    expect(page.get_by_test_id("code-pane-path")).to_have_text("../evil.py")
    name2, size2, _ = _dl(page, page.get_by_test_id("file-download-button"))
    assert size2 > 0, (name2, size2)
    name, size, p = _dl(page, page.get_by_test_id("download-zip-button"))
    names = zipfile.ZipFile(p).namelist()
    assert size > 0 and "app/main.py" in names and not any(".." in n for n in names), (name, size, names)
    info.append(f"{MODE}: file {name2} {size2} B; zip {name} {size} B {names}")
    page.get_by_test_id("detail-tab-steps").click(); expect(page.get_by_test_id("step-item-3")).to_have_attribute("data-status", "done")
    page.get_by_test_id("detail-tab-log").click(); expect(page.get_by_test_id("log-panel")).to_be_visible()

def flow_clone(page):
    """Clone C while a stub-slow job runs, so the clone lands in the queue."""
    wait_idle()
    page.goto(BASE + "/?tab=create")
    expect(page.get_by_test_id("model-input")).not_to_have_value("")
    create_job(page, f"R{MODE[0]}", "stub-slow", 3)
    src = state["jobs"]["C"]
    page.goto(f"{BASE}/?tab=history&job={src}")
    page.get_by_test_id("clone-job-button").click()
    sheet = page.get_by_test_id("clone-job-sheet")
    expect(sheet).to_be_visible()
    srcj = api(f"/jobs/{src}")
    expect(sheet.get_by_test_id("model-input")).to_have_value(srcj["model"])
    expect(sheet.get_by_test_id("arena-url-input")).to_have_value(srcj["arena_url"])
    expect(sheet.get_by_test_id("roadmap-input")).to_have_value(srcj["roadmap_md"])
    expect(sheet.get_by_test_id("steps-found")).to_have_text("3 steps found")
    key = f"K{MODE[0]}"; new_title = f"test_{SUF} {key} clone"
    rm = sheet.get_by_test_id("roadmap-input")
    rm.fill(srcj["roadmap_md"].replace(f"# test_{SUF} C", f"# {new_title}", 1) + "\n### test step 4\nExtra.\n")
    sheet.get_by_test_id("model-input").fill("gpt-4o")
    sheet.get_by_test_id("project-context-input").fill("test clone context")
    expect(sheet.get_by_test_id("steps-found")).to_have_text("4 steps found")
    expect(sheet.get_by_test_id("clone-edited-note")).to_be_visible()
    expect(rm).to_have_value(re.compile(new_title))
    sub = sheet.get_by_test_id("start-job-button")
    expect(sub).to_have_text(re.compile("Add clone to queue"))
    clickable(sub)
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
        sub.click()
    assert resp.value.status == 201, resp.value.text()
    d = resp.value.json(); state["jobs"][key] = d["job_id"]; state.setdefault("all", []).append(d["job_id"]); save()
    assert d["status"] == "queued", d
    expect(sheet).to_be_hidden()
    expect(page).to_have_url(re.compile(f"job={d['job_id']}"))
    expect(page.get_by_test_id("history-detail-title")).to_have_text(new_title)
    expect(page.get_by_test_id("history-detail").get_by_text("clone of", exact=False).first).to_be_visible()
    tab(page, "queue")
    expect(page.get_by_test_id(f"queue-row-{d['job_id']}")).to_contain_text(new_title)
    expect(page.get_by_test_id(f"queue-row-{d['job_id']}")).to_contain_text("4 steps")
    j = api(f"/jobs/{d['job_id']}")
    assert j["cloned_from"] == src and j["model"] == "gpt-4o" and j["project_context"] == "test clone context", j

def flow_settings(page):
    before = api("/settings")
    page.goto(BASE + "/?tab=settings")
    expect(page.get_by_test_id("setting-model-input")).to_have_value(before["model"])
    save_btn = page.get_by_test_id("settings-save")
    expect(save_btn).to_be_disabled()
    page.get_by_test_id("setting-step_delay_seconds-input").fill("700")
    expect(page.get_by_test_id("setting-step_delay_seconds-error")).to_be_visible(); expect(save_btn).to_be_disabled()
    page.get_by_test_id("setting-model-input").fill("")
    expect(page.get_by_test_id("setting-model-error")).to_be_visible()
    page.get_by_test_id("setting-model-input").fill(f"test_{SUF}-model")
    page.get_by_test_id("setting-step_delay_seconds-input").fill("1")
    page.get_by_test_id("setting-request_timeout_seconds-input").fill("120")
    clickable(save_btn); save_btn.click()
    expect(page.get_by_text("Settings saved")).to_be_visible()
    page.reload()
    expect(page.get_by_test_id("setting-model-input")).to_have_value(f"test_{SUF}-model")
    expect(page.get_by_test_id("setting-step_delay_seconds-input")).to_have_value("1")
    expect(page.get_by_test_id("setting-request_timeout_seconds-input")).to_have_value("120")
    tab(page, "create"); expect(page.get_by_test_id("model-input")).to_have_value(f"test_{SUF}-model")
    expect(page.get_by_test_id("run-settings-hint")).to_contain_text("1s pause")
    tab(page, "settings")
    page.get_by_test_id("settings-reset").click()
    page.get_by_test_id("settings-reset-yes").click()
    env = before["env_defaults"]
    expect(page.get_by_test_id("setting-model-input")).to_have_value(env["model"])
    page.reload()
    expect(page.get_by_test_id("setting-model-input")).to_have_value(env["model"])
    expect(page.get_by_test_id("setting-step_delay_seconds-input")).to_have_value(str(env["step_delay_seconds"]))
    tab(page, "create"); expect(page.get_by_test_id("model-input")).to_have_value(env["model"])

def flow_mobile_clickables(page):
    """Mobile: top-level tabs and header controls are reachable and not covered."""
    page.goto(BASE + "/?tab=create")
    for t in ("create", "queue", "current", "history", "settings"):
        loc = page.get_by_test_id(f"tab-{t}"); clickable(loc); loc.click()
        expect(page).to_have_url(re.compile(f"tab={t}"))
    w = page.evaluate("document.documentElement.scrollWidth"); info.append(f"mobile scrollWidth={w}")
    assert w <= 392, f"horizontal overflow: page is {w}px wide"

if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for name in sys.argv[2:]:
            ctx = b.new_context(viewport=VIEWPORTS[MODE], accept_downloads=True, is_mobile=MODE == "mobile", has_touch=MODE == "mobile")
            page = ctx.new_page(); instrument(page)
            try:
                globals()["flow_" + name](page); print(f"PASS {MODE}:{name}")
            except Exception as e:
                page.screenshot(path=f"{SHOTS}/{MODE}_{name}.png", full_page=True)
                print(f"FAIL {MODE}:{name}: {str(e)[:1500]}")
            ctx.close()
        b.close()
    for i in info: print("   info:", i)
    print("CONSOLE:", dict(console) or None); print("REQUESTFAILED:", dict(netfail) or None)
    print("API>=400:", dict(api_bad) or None); print("WRONG HOST/PREFIX:", dict(wrong) or None)
