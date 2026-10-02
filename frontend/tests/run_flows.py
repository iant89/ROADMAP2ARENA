"""ROADMAP2ARENA frontend flows. Usage: run_flows.py flow1 flow2 ...  (or 'all')."""
import json, os, random, re, string, sys, zipfile, io, time
from collections import Counter
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8080"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = "/tmp/r2a_test_state.json"  # outside the Vite root: writes under /app/frontend trigger Vite full-reload
SHOTS = "/tmp/screenshots"
IGNORE = re.compile(r"React DevTools|favicon\.ico", re.I)

def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"jobs": [], "suffix": "".join(random.choices(string.ascii_lowercase + string.digits, k=5))}

state = load_state()
def save_state(): json.dump(state, open(STATE, "w"), indent=1)
SUF = state["suffix"]; save_state()

console, netfail, api_bad, wrong_host = Counter(), Counter(), Counter(), Counter()

def instrument(page):
    page.set_default_timeout(10_000)
    page.on("console", lambda m: (m.type in ("error", "warning") or "vite" in m.text) and not IGNORE.search(m.text) and console.update([f"{m.type}: {m.text[:200]}"]))
    page.on("pageerror", lambda e: console.update([f"pageerror: {str(e)[:200]}"]))
    def onfail(r):
        if not IGNORE.search(r.url): netfail.update([f"{r.method} {r.url} {r.failure}"])
    page.on("requestfailed", onfail)
    def onresp(r):
        u = r.url
        if "/api/" in u and r.status >= 400: api_bad.update([f"{r.request.method} {re.sub(r'[0-9a-f-]{36}', '<id>', u)} -> {r.status}"])
    page.on("response", onresp)
    def onreq(r):
        u = r.url
        if u.startswith("data:") or u.startswith("blob:"): return
        if not u.startswith(BASE): wrong_host.update([u[:120]])
    page.on("request", onreq)
    if os.environ.get("DBG"):
        page.on("console", lambda m: print("   con", time.strftime('%X'), m.type, m.text[:150]))
        page.on("request", lambda r: r.is_navigation_request() and print("   navreq", time.strftime('%X'), r.url))
        page.on("websocket", lambda ws: ws.on("framereceived", lambda f: print("   wsrecv", time.strftime('%X'), str(f)[:200])))
        page.on("framenavigated", lambda f: f == page.main_frame and print(f"   nav: {time.strftime('%X')} {f.url}"))

def track_job(page):
    def onresp(r):
        if r.request.method == "POST" and r.url.endswith("/api/jobs") and r.status == 201:
            jid = r.json()["job_id"]; state["jobs"].append(jid); state["last_job"] = jid; save_state()
    page.on("response", onresp)

def roadmap(n, title):
    return f"# {title}\n\n" + "\n".join(f"### test step {i}\nCreate file {i}.\n" for i in range(1, n + 1))

def wait_job_finished(page, timeout=90_000):
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Done|Error", re.I), timeout=timeout)

# ------------------------------------------------------------------ flows
def flow_load_page(page):
    page.goto(BASE + "/")
    expect(page.get_by_role("heading", name="ROADMAP2ARENA")).to_be_visible()
    expect(page.get_by_test_id("arena-url-input")).to_have_value("http://localhost:9090")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    expect(page.get_by_test_id("steps-found")).to_have_text("No steps found")
    expect(page.get_by_test_id("empty-roadmap-hint")).to_be_visible()
    expect(page.get_by_test_id("artifacts-empty")).to_be_visible()
    expect(page.get_by_test_id("start-job-button")).to_be_disabled()
    expect(page.get_by_test_id("download-zip-button")).to_be_disabled()
    page.get_by_test_id("tab-transcript").click(); expect(page.get_by_test_id("transcript-empty")).to_be_visible()
    page.get_by_test_id("tab-log").click(); expect(page.get_by_test_id("log-empty")).to_be_visible()
    expect(page.get_by_test_id("backend-error-banner")).to_have_count(0)

def flow_sample_preview(page):
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    with page.expect_response(lambda r: r.url.endswith("/api/roadmap/parse")) as resp:
        page.get_by_test_id("load-sample-button").click()
    assert resp.value.status == 200, resp.value.status
    n = len(resp.value.json()["steps"])
    expect(page.get_by_test_id("steps-found")).to_have_text(f"{n} steps found")
    expect(page.get_by_test_id("project-context-input")).not_to_have_value("")
    for i in range(1, n + 1): expect(page.get_by_test_id(f"step-item-{i}")).to_be_visible()
    expect(page.get_by_test_id("start-job-button")).to_be_enabled()
    # edit -> preview updates
    page.get_by_test_id("roadmap-input").fill(roadmap(2, f"test_{SUF} preview"))
    expect(page.get_by_test_id("steps-found")).to_have_text("2 steps found")
    expect(page.get_by_test_id("step-item-3")).to_have_count(0)
    page.get_by_test_id("roadmap-input").fill("just text, no steps")
    expect(page.get_by_test_id("steps-found")).to_have_text("No steps found")
    expect(page.get_by_test_id("start-job-button")).to_be_disabled()
    print(f"   info: sample has {n} steps")

def flow_validation(page):
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    page.get_by_test_id("roadmap-input").fill(roadmap(2, f"test_{SUF} validation"))
    expect(page.get_by_test_id("steps-found")).to_have_text("2 steps found")
    problems = []
    # bad scheme
    page.get_by_test_id("arena-url-input").fill("ftp://localhost:9090")
    start = page.get_by_test_id("start-job-button")
    if start.is_enabled():
        with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
            start.click()
        if resp.value.status != 422: problems.append(f"bad scheme POST status {resp.value.status}")
        try: expect(page.get_by_text("Could not start job")).to_be_visible()
        except AssertionError: problems.append("no error shown for bad URL scheme")
    else:
        if page.get_by_text(re.compile("http", re.I)).filter(has_text=re.compile("must|invalid", re.I)).count() == 0:
            problems.append("bad scheme: start disabled but no message")
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    # empty model
    page.get_by_test_id("arena-url-input").fill("http://localhost:9090")
    for val, label in (("", "empty"), ("   ", "whitespace")):
        page.get_by_test_id("model-input").fill(val)
        page.wait_for_timeout(300)  # debounce-free field; tiny settle only for render
        enabled = start.is_enabled()
        msg = page.get_by_text(re.compile(r"model (is )?(required|cannot|must)|enter a model", re.I)).count()
        if enabled:
            with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
                start.click()
            if resp.value.status != 422: problems.append(f"{label} model POST status {resp.value.status}")
            try: expect(page.get_by_text("Could not start job").last).to_be_visible()
            except AssertionError: problems.append(f"{label} model: no error shown")
        elif msg == 0:
            problems.append(f"{label} model: Start disabled silently, no error message explaining why")
    # empty url
    page.get_by_test_id("model-input").fill("gpt-4o")
    page.get_by_test_id("arena-url-input").fill("")
    if start.is_enabled(): problems.append("empty URL: start enabled")
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    assert not problems, "; ".join(problems)

def flow_validation_inline(page):
    """F-001 retest: inline messages after edit, Start disabled, nothing POSTed, messages clear when valid."""
    posts = []
    page.on("request", lambda r: r.method == "POST" and r.url.endswith("/api/jobs") and posts.append(r.url))
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    start = page.get_by_test_id("start-job-button")
    mErr, uErr = page.get_by_test_id("model-error"), page.get_by_test_id("arena_url-error")
    expect(mErr).to_have_count(0); expect(uErr).to_have_count(0)  # not on first load
    page.get_by_test_id("roadmap-input").fill(roadmap(2, f"test_{SUF} validation"))
    expect(page.get_by_test_id("steps-found")).to_have_text("2 steps found")
    expect(start).to_be_enabled()
    model, url = page.get_by_test_id("model-input"), page.get_by_test_id("arena-url-input")
    for val in ("", "   "):
        model.fill(val)
        expect(mErr).to_have_text("Model is required"); expect(mErr).to_be_visible()
        expect(model).to_have_attribute("aria-invalid", "true")
        expect(start).to_be_disabled()
    model.fill("gpt-4o")
    expect(mErr).to_have_count(0); expect(start).to_be_enabled()
    for val, msg in (("ftp://localhost:9090", "Use an http:// or https:// URL"), ("localhost:9090", "Use an http:// or https:// URL"), ("", "required")):
        url.fill(val)
        expect(uErr).to_contain_text(msg); expect(uErr).to_be_visible()
        expect(start).to_be_disabled()
    # both errors at once
    model.fill("")
    expect(mErr).to_be_visible(); expect(uErr).to_be_visible()
    url.fill("http://localhost:9090"); model.fill("gpt-4o")
    expect(uErr).to_have_count(0); expect(mErr).to_have_count(0); expect(start).to_be_enabled()
    start.click(force=False, trial=True)  # clickable, not covered
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    assert not posts, f"POST /api/jobs sent during validation: {posts}"

def flow_run_job(page):
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    page.get_by_test_id("load-sample-button").click()
    expect(page.get_by_test_id("steps-found")).to_have_text(re.compile(r"\d+ steps found"))
    rm = page.get_by_test_id("roadmap-input")
    rm.fill(rm.input_value().replace("# Tasky - FastAPI todo API", f"# test_{SUF} Tasky run", 1))
    if os.environ.get("RUN_MODEL"): page.get_by_test_id("model-input").fill(os.environ["RUN_MODEL"])
    expect(page.get_by_test_id("steps-found")).to_have_text(re.compile(r"[1-9]\d* steps found"))
    n = int(page.get_by_test_id("steps-found").inner_text().split()[0])
    track_job(page)
    page.get_by_test_id("start-job-button").click()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Running", re.I))
    expect(page.get_by_test_id("start-job-button")).to_have_count(0)  # form collapsed
    seen = {i: set() for i in range(1, n + 1)}
    dl_enabled_at = None; log_counts = set(); deadline = time.time() + 90
    while time.time() < deadline:
        for i in range(1, n + 1):
            s = page.get_by_test_id(f"step-item-{i}").get_attribute("data-status"); seen[i].add(s)
        done = sum("done" in seen[i] for i in seen)
        snap = page.evaluate("""() => [document.querySelector('[data-testid=job-progress]').innerText,
            !document.querySelector('[data-testid=download-zip-button]').disabled,
            document.querySelectorAll('[data-testid^=step-item-][data-status=done]').length]""")
        if dl_enabled_at is None and snap[1]:
            dl_enabled_at = snap[2]  # done steps in the same DOM snapshot
        log_counts.add(page.get_by_test_id("tab-log").inner_text())
        if page.get_by_test_id("job-status-badge").first.inner_text().strip().lower() in ("done", "error"): break
        page.wait_for_timeout(250)  # polling sampler interval
    for i in range(1, n + 1): seen[i].add(page.get_by_test_id(f"step-item-{i}").get_attribute("data-status"))
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Done", re.I))
    expect(page.get_by_test_id("job-done-alert")).to_be_visible()
    expect(page.get_by_test_id("job-progress")).to_contain_text(f"{n} / {n} steps")
    print(f"   info: job {state['last_job']} statuses seen {[sorted(v) for v in seen.values()]}; download enabled when done={dl_enabled_at}; log tab labels seen={len(log_counts)}")
    probs = []
    if not all("running" in seen[i] for i in seen): probs.append("some steps never observed running")
    if not all("pending" in seen[i] for i in range(2, n + 1)): probs.append("later steps not observed pending")
    if dl_enabled_at not in (1, 2): probs.append(f"download enabled at done={dl_enabled_at}")
    if len(log_counts) < 3: probs.append("log count did not update live")
    assert not probs, "; ".join(probs)
    state["done_job"] = state["last_job"]; state["done_steps"] = n; save_state()

def _open_done(page):
    jid = state["done_job"]
    page.goto(f"{BASE}/?job={jid}")
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Done", re.I))
    return jid

def flow_artifacts(page):
    _open_done(page)
    page.get_by_test_id("tab-artifacts").click()
    expect(page.get_by_test_id("artifacts-panel")).to_be_visible()
    items = page.locator("[data-testid^=file-tree-item-]")
    assert items.count() >= 3, f"only {items.count()} files"
    item = page.get_by_test_id("file-tree-item-app/models.py")
    item.click()
    expect(page.get_by_test_id("code-pane-path")).to_have_text("app/models.py")
    expect(page.get_by_test_id("code-pane-step")).to_contain_text(re.compile(r"from step \d+: \S"))
    expect(page.get_by_test_id("code-pane")).to_contain_text("class Item(BaseModel)")
    page.get_by_test_id("file-tree-item-app/main.py").click()
    expect(page.get_by_test_id("code-pane-path")).to_have_text("app/main.py")
    expect(page.get_by_test_id("code-pane")).to_contain_text("from fastapi import FastAPI")
    expect(page.get_by_test_id("file-tree-item-../evil.py")).to_contain_text("not in ZIP")

def flow_transcript(page):
    _open_done(page)
    page.get_by_test_id("tab-transcript").click()
    expect(page.get_by_test_id("transcript-panel")).to_be_visible()
    n = state["done_steps"]
    expect(page.locator("[data-testid^=transcript-step-]")).to_have_count(n)
    s1 = page.get_by_test_id("transcript-step-1")
    expect(s1.get_by_text("Prompt", exact=True)).to_have_count(0)
    s1.get_by_role("button").first.click()
    expect(s1.get_by_text("Prompt", exact=True)).to_be_visible()
    expect(s1.get_by_text("Response", exact=True)).to_be_visible()
    expect(s1).to_contain_text("app/main.py")
    s1.get_by_role("button").first.click()
    expect(s1.get_by_text("Prompt", exact=True)).to_have_count(0)

def flow_log(page):
    _open_done(page)
    page.get_by_test_id("tab-log").click()
    expect(page.get_by_test_id("log-panel")).to_be_visible()
    txt = page.get_by_test_id("log-panel").inner_text()
    assert re.search(r"\d\d:\d\d:\d\d", txt) and re.search(r"(?i)\bok\b|info", txt), txt[:300]
    print(f"   info: log lines={len(txt.splitlines())//3 or len(txt.splitlines())}")

def flow_download_zip(page):
    jid = _open_done(page)
    btn = page.get_by_test_id("download-zip-button")
    expect(btn).to_be_enabled()
    with page.expect_download(timeout=20_000) as d:
        btn.click()
    path = d.value.path(); name = d.value.suggested_filename
    zf = zipfile.ZipFile(path); names = zf.namelist()
    assert name.endswith(".zip"), name
    assert "app/main.py" in names and not any(".." in x for x in names), names
    expect(page.get_by_text("ZIP downloaded")).to_be_visible()
    print(f"   info: {name} -> {names}")

def flow_deep_link_reload(page):
    jid = _open_done(page)
    expect(page.get_by_text(jid)).to_be_visible()
    page.reload()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Done", re.I))
    expect(page.get_by_text(jid)).to_be_visible()
    expect(page.get_by_test_id(f"step-item-{state['done_steps']}")).to_have_attribute("data-status", "done")

def flow_recent_jobs(page):
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    page.get_by_test_id("recent-jobs-button").click()
    sheet = page.get_by_test_id("recent-jobs-sheet")
    expect(sheet).to_be_visible()
    jid = state["done_job"]
    card = page.get_by_test_id(f"recent-job-{jid}")
    expect(card).to_contain_text(f"test_{SUF}")
    card.click()
    expect(sheet).to_be_hidden()
    expect(page.get_by_text(jid)).to_be_visible()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Done", re.I))
    page.get_by_test_id("new-job-button").click()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    expect(page.get_by_test_id("start-job-button")).to_be_visible()

def flow_error_job(page):
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    page.get_by_test_id("model-input").fill("stub-503-at-3")
    page.get_by_test_id("roadmap-input").fill(roadmap(5, f"test_{SUF} error"))
    expect(page.get_by_test_id("steps-found")).to_have_text("5 steps found")
    track_job(page)
    page.get_by_test_id("start-job-button").click()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Running", re.I))
    wait_job_finished(page)
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Error", re.I))
    for i, st in ((1, "done"), (2, "done"), (3, "error"), (4, "pending"), (5, "pending")):
        expect(page.get_by_test_id(f"step-item-{i}")).to_have_attribute("data-status", st)
    expect(page.get_by_test_id("step-error-message")).to_contain_text("503")
    alert = page.get_by_test_id("job-error-alert")
    expect(alert).to_contain_text("Step 3 failed")
    expect(alert).to_contain_text(re.compile("Chrome tab", re.I))
    expect(page.get_by_test_id("download-zip-button")).to_be_enabled()
    expect(page.get_by_text("Step 3 failed").last).to_be_visible()
    state["error_job"] = state["last_job"]; save_state()
    page.reload()  # error persisted via deep link?
    print(f"   info: url after start = {page.url}")

def flow_url_after_start(page):
    """Persistence: does reloading during/after a started job keep it (URL carries ?job=)?"""
    jid = state["error_job"]
    page.goto(f"{BASE}/?job={jid}")
    expect(page.get_by_test_id("job-error-alert")).to_contain_text("Chrome tab")
    expect(page.get_by_test_id("step-item-4")).to_have_attribute("data-status", "pending")

def flow_routes(page):
    page.goto(BASE + "/some/unknown/route")
    expect(page.get_by_role("heading", name="ROADMAP2ARENA")).to_be_visible()
    page.goto(BASE + "/?job=not-a-real-id")
    expect(page.get_by_text("Could not open job")).to_be_visible()
    expect(page.get_by_role("heading", name="ROADMAP2ARENA")).to_be_visible()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Idle", re.I))
    page.goto(BASE + "/?job=00000000-0000-4000-8000-000000000000")
    expect(page.get_by_text("Could not open job")).to_be_visible()
    expect(page.get_by_test_id("start-job-button")).to_be_visible()

def flow_reload_after_start(page):
    """Start a job, then reload: is the job still shown?"""
    page.goto(BASE + "/")
    expect(page.get_by_test_id("model-input")).to_have_value("gpt-4o")
    page.get_by_test_id("roadmap-input").fill(roadmap(1, f"test_{SUF} reload"))
    expect(page.get_by_test_id("steps-found")).to_have_text("1 step found")
    track_job(page)
    page.get_by_test_id("start-job-button").click()
    expect(page.get_by_test_id("job-status-badge").first).to_have_text(re.compile("Running|Done", re.I))
    url = page.url
    wait_job_finished(page)
    page.reload()
    expect(page.get_by_role("heading", name="ROADMAP2ARENA")).to_be_visible()
    page.wait_for_load_state("networkidle")
    shown = page.get_by_text(state["last_job"]).count()
    assert shown, f"after reload the started job is gone (URL was {url}, no ?job= param); user must use Recent jobs"

FLOWS = [n[5:] for n in dir() if n.startswith("flow_")]
ORDER = ["load_page", "sample_preview", "validation", "run_job", "artifacts", "transcript", "log", "download_zip",
         "deep_link_reload", "recent_jobs", "error_job", "url_after_start", "routes", "reload_after_start"]

if __name__ == "__main__":
    names = ORDER if sys.argv[1:] in ([], ["all"]) else sys.argv[1:]
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for name in names:
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page = ctx.new_page(); instrument(page)
            try:
                globals()["flow_" + name](page); print(f"PASS {name}")
            except Exception as e:
                page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)
                print(f"FAIL {name}: {str(e).splitlines()[0][:400]}")
            ctx.close()
        b.close()
    print("CONSOLE:", dict(console) or None)
    print("REQUESTFAILED:", dict(netfail) or None)
    print("API>=400:", dict(api_bad) or None)
    print("WRONG HOST:", dict(wrong_host) or None)
