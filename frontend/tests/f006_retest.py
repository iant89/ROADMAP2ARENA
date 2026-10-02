"""F-006 retest: jobs must survive (no uvicorn reload), repos under /app/data/repos. Usage: f006_retest.py <step>"""
import json, os, re, sys, time, random, string, subprocess, urllib.request
from collections import Counter
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:8080"; DIR = "/tmp/r2a5"; STATE = f"{DIR}/state.json"; SHOTS = "/tmp/frontend-test"
state = json.load(open(STATE)) if os.path.exists(STATE) else {"suffix": "".join(random.choices(string.ascii_lowercase + string.digits, k=5)), "jobs": {}, "all": [], "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}
def save(): json.dump(state, open(STATE, "w"), indent=1)
save(); SUF = state["suffix"]
STEP = sys.argv[1]; MOBILE = STEP == "mobile"
console, netfail, api_bad, info = Counter(), Counter(), Counter(), []

def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + "/api" + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
def worker_pid():
    out = subprocess.run(["pgrep", "-f", "multiprocessing.spawn"], capture_output=True, text=True).stdout.split()
    par = subprocess.run(["pgrep", "-f", "uvicorn server:app"], capture_output=True, text=True).stdout.split()
    kids = [p for p in out if open(f"/proc/{p}/stat").read().split()[3] in par]
    return kids
def log_lines(): return sum(1 for _ in open("/var/log/supervisor/backend.err.log"))
def new_log(since): return [l.rstrip() for i, l in enumerate(open("/var/log/supervisor/backend.err.log")) if i >= since]
def repo_check(jid):
    a = os.path.isdir(f"/app/data/repos/{jid}"); b = os.path.exists("/app/backend/data/repos") and os.listdir("/app/backend/data/repos")
    return a, b
def wait_status(jid, statuses, timeout):
    end = time.time() + timeout
    while time.time() < end:
        d = api(f"/jobs/{jid}")
        if d["status"] in statuses: return d
        time.sleep(1)
    raise AssertionError(f"{jid} still {d['status']} after {timeout}s")
def roadmap(n, title): return f"# {title}\n\n" + "\n".join(f"### test step {i}\nCreate file {i}.\n" for i in range(1, n + 1))

def create_job(page, key, model, n):
    title = f"test_{SUF} {key}"
    page.goto(BASE + "/?tab=create"); expect(page.get_by_test_id("model-input")).not_to_have_value("")
    page.get_by_test_id("model-input").fill(model); page.get_by_test_id("roadmap-input").fill(roadmap(n, title))
    expect(page.get_by_test_id("steps-found")).to_have_text(f"{n} steps found")
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as resp:
        page.get_by_test_id("start-job-button").click()
    assert resp.value.status == 201, resp.value.text()[:200]
    jid = resp.value.json()["job_id"]; state["jobs"][key] = jid; state["all"].append(jid); save(); return jid

def git_tab(page, jid, n):
    page.goto(f"{BASE}/?tab=history&job={jid}")
    page.get_by_test_id("detail-tab-git").click()
    expect(page.get_by_test_id("git-panel")).to_be_visible()
    expect(page.get_by_test_id("git-commit-count")).to_have_text(f"{n} commit{'s' if n != 1 else ''}", timeout=10_000)
    msgs = page.locator("[data-testid=git-commit-list] li").all_inner_texts()
    assert len(msgs) == n and all(re.search(rf"Step {i}: test step {i}", " ".join(msgs)) for i in range(1, n + 1)), msgs
    g = api(f"/jobs/{jid}/git"); info.append(f"git api for {jid[:8]}: {json.dumps({k: g.get(k) for k in ('commit_count', 'path', 'head') if k in g})[:160]}")

def run_job_check(page, key, model, n, final, timeout):
    pid0, l0 = worker_pid(), log_lines()
    jid = create_job(page, key, model, n)
    d = wait_status(jid, final, timeout)
    pid1, nl = worker_pid(), new_log(l0)
    reloads = [l for l in nl if re.search(r"Reloading|detected change|interrupted|Shutting down", l)]
    info.append(f"{key}: status={d['status']} steps_done={d.get('steps_done')} failed_step={d.get('failed_step')} error={str(d.get('error'))[:110]!r} worker {pid0}->{pid1}")
    assert not reloads, f"reload during {key}: {reloads[:3]}"
    assert pid0 == pid1, f"backend worker changed {pid0}->{pid1}"
    assert "restart" not in str(d.get("error") or "").lower(), d.get("error")
    a, b = repo_check(jid); assert a, f"no /app/data/repos/{jid}"; assert not b, f"something under /app/backend/data/repos: {b}"
    return jid, d

def flow_full(page):
    jid, d = run_job_check(page, "J4", "stub-slow", 4, ["done", "error", "stopped"], 100)
    assert d["status"] == "done" and d["steps_done"] == 4, d["status"]
    git_tab(page, jid, 4)

def flow_fail(page):
    jid, d = run_job_check(page, "E1", "stub-503-at-3", 4, ["done", "error", "stopped"], 60)
    assert d["status"] == "error" and d.get("failed_step") == 3 and "503" in str(d.get("error")), (d["status"], d.get("failed_step"), d.get("error"))
    git_tab(page, jid, 2)
    page.goto(f"{BASE}/?tab=history&job={jid}")
    expect(page.get_by_test_id("history-detail")).to_contain_text(re.compile(r"503|Failed", re.I))

def flow_resume(page):
    jid = state["jobs"]["E1"]; pid0, l0 = worker_pid(), log_lines()
    page.goto(f"{BASE}/?tab=history&job={jid}")
    page.get_by_test_id("toggle-overrides-button").click()
    page.get_by_test_id("override-model-input").fill("stub-slow")
    with page.expect_response(lambda r: "/resume" in r.url and r.request.method == "POST") as resp:
        page.get_by_test_id("resume-job-button").click()
    assert resp.value.status < 300, resp.value.text()[:200]
    rj = resp.value.json(); rid = rj.get("job_id", jid); info.append(f"resume response: {json.dumps(rj)[:160]}")
    if rid != jid: state["all"].append(rid); state["jobs"]["E1r"] = rid; save()
    d = wait_status(rid, ["done", "error", "stopped"], 80)
    nl = new_log(l0); reloads = [l for l in nl if re.search(r"Reloading|detected change|interrupted|Shutting down", l)]
    info.append(f"resume: status={d['status']} steps_done={d.get('steps_done')} error={str(d.get('error'))[:100]!r} worker {pid0}->{worker_pid()}")
    assert not reloads, reloads[:3]; assert pid0 == worker_pid()
    assert d["status"] == "done" and d["steps_done"] == 4, (d["status"], d.get("error"))
    a, b = repo_check(rid); assert a and not b, (a, b)
    git_tab(page, rid, 4)

def flow_mobile(page):
    jid = state["jobs"]["J4"]
    page.goto(BASE + "/?tab=history"); expect(page.get_by_test_id("history-panel")).to_be_visible()
    page.get_by_test_id(f"history-job-{jid}").click()
    expect(page.get_by_test_id("history-detail")).to_be_visible()
    t = page.get_by_test_id("detail-tab-git"); expect(t).to_be_in_viewport(); t.click()
    expect(page.get_by_test_id("git-commit-count")).to_have_text("4 commits")
    page.locator("[data-testid=git-commit-list] li button").first.click()
    expect(page.get_by_test_id("git-commit-diff")).to_be_visible()
    w = page.evaluate("document.documentElement.scrollWidth"); assert w <= 391, f"page {w}px wide"

FLOWS = {"full": flow_full, "fail": flow_fail, "resume": flow_resume, "mobile": flow_mobile}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844} if MOBILE else {"width": 1280, "height": 800}, is_mobile=MOBILE, has_touch=MOBILE)
    page = ctx.new_page(); page.set_default_timeout(10_000)
    page.on("console", lambda m: m.type == "error" and console.update([m.text[:120]]))
    page.on("pageerror", lambda e: console.update(["PAGEERROR " + str(e)[:120]]))
    page.on("requestfailed", lambda r: netfail.update([f"{r.method} {r.url[:90]} {r.failure}"]))
    page.on("response", lambda r: "/api/" in r.url and r.status >= 400 and api_bad.update([f"{r.request.method} {r.url[:90]} -> {r.status}"]))
    page.on("request", lambda r: (not r.url.startswith(BASE) and not r.url.startswith("data:") and "fonts." not in r.url) and netfail.update(["WRONG HOST " + r.url[:90]]))
    try:
        FLOWS[STEP](page); print(f"PASS {STEP}")
    except Exception as e:
        print(f"FAIL {STEP}: {str(e)[:600]}"); page.screenshot(path=f"{SHOTS}/{'mobile' if MOBILE else 'desktop'}_f006_{STEP}.png", full_page=True)
    b.close()
for x in info: print("  info:", x)
print("CONSOLE:", dict(console) or None); print("NETFAIL:", dict(netfail) or None); print("API>=400:", dict(api_bad) or None)
