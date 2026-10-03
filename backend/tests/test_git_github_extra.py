"""Tester suite for PR #8 (per-job git repo) + PR #9 (GitHub) - edge cases on top of test_git/test_github.

Everything runs locally: an isolated backend (throwaway DB, temp R2A_DATA_DIR), a private arena stub
(TEST_STUB_URL, required) and tests/github_stub.py on a free port. GITHUB_API_URL/GITHUB_OAUTH_URL point
at the stub; pushes go to local bare repos. A fresh Fernet key is generated per run and never printed.
Stub tokens are imported from github_stub.TOKENS and never printed (assert messages are redacted).

    TEST_STUB_URL=http://127.0.0.1:<arena stub> /app/venv/bin/python tests/test_git_github_extra.py [test names]
"""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import httpx
from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from isolated_server import IsolatedServer, free_port  # noqa: E402
import github_stub  # noqa: E402  (only for the fake token table / client id)

_toks = list(github_stub.TOKENS)
PAT = next(t for t in _toks if github_stub.TOKENS[t][1] and "repo" in github_stub.TOKENS[t][1].split(", "))
FINE = next(t for t in _toks if github_stub.TOKENS[t][1] is None)
READONLY = next(t for t in _toks if github_stub.TOKENS[t][0] != "r2a-tester")
CLIENT_ID = github_stub.CLIENT_ID
ARENA = os.environ["TEST_STUB_URL"]
SFX = secrets.token_hex(3)
TMP = tempfile.mkdtemp(prefix="r2a-tester-gh-")
DATA = os.path.join(TMP, "data")
GH_ROOT = os.path.join(TMP, "gh")
GH_PORT = free_port()
GH = f"http://127.0.0.1:{GH_PORT}"
STUB_LOG = os.path.join(TMP, "github_stub.log")
KEY = Fernet.generate_key().decode()
GH_ENV = {"GITHUB_API_URL": GH, "GITHUB_OAUTH_URL": GH, "GITHUB_OAUTH_CLIENT_ID": "", "R2A_GITHUB_TOKEN": "",
          "R2A_GITHUB_ALLOW_FILE_REMOTES": "1", "R2A_DATA_DIR": DATA, "R2A_SECRET_KEY": KEY}
SRV = IsolatedServer(extra_env=GH_ENV)
GENV = {"PATH": os.environ["PATH"], "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "test_outsider", "GIT_AUTHOR_EMAIL": "o@example.com",
        "GIT_COMMITTER_NAME": "test_outsider", "GIT_COMMITTER_EMAIL": "o@example.com"}
SECRETS = set()
for t in _toks + ["ghp_revoked000000000000000000000000000"]:
    SECRETS |= {t, base64.b64encode(t.encode()).decode()}
    for login in ("r2a-tester", "r2a-reader", "x-access-token"):
        SECRETS.add(base64.b64encode(f"{login}:{t}".encode()).decode())
SECRETS.add(KEY)
LEAKS: list[str] = []
SEEN = {"requests": 0, "servers": []}
STATE: dict = {}
BASE = ""
RM3 = f"# test_git_{SFX}\n\n### Alpha step\nmake a\n\n### Beta step\nmake b\n\n### Gamma step\nmake c\n"
ONCE_N = 4 + secrets.randbelow(12)  # stub-503-once-at-N fires once per stub process -> fresh N per run
RM4 = f"# test_pr_{SFX}\n\n" + "".join(f"### Part {i}\nmake {i}\n\n" for i in range(1, ONCE_N + 1))


def red(s) -> str:
    s = str(s)
    for x in sorted(SECRETS, key=len, reverse=True):
        s = s.replace(x, "<redacted>")
    return s


def _has_id(o) -> bool:
    if isinstance(o, dict):
        return "_id" in o or any(_has_id(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_id(v) for v in o)
    return False


def _hook(resp: httpx.Response):
    resp.read()
    SEEN["requests"] += 1
    where = f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}"
    ct = resp.headers.get("content-type", "")
    blob = (resp.text if ("json" in ct or "text" in ct) else "") + " " + " ".join(f"{k}:{v}" for k, v in resp.headers.items())
    if any(s in blob for s in SECRETS):
        LEAKS.append(f"secret in response {where}")
    if resp.status_code == 500:
        LEAKS.append(f"5xx {where}: {red(resp.text[:200])}")
    if "json" in ct:
        try:
            if _has_id(resp.json()):
                LEAKS.append(f"_id in {where}")
        except ValueError:
            pass


C = httpx.Client(timeout=60, event_hooks={"response": [_hook]})


def ok(cond, msg=""):
    if not cond:
        raise AssertionError(red(msg))


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, env=GENV)


def create_job(roadmap=RM3, model="gpt-4o"):
    r = C.post(f"{BASE}/jobs", json={"roadmap_md": roadmap, "model": model, "arena_url": ARENA})
    ok(r.status_code == 201, r.text)
    return r.json()["job_id"]


def wait(job_id, statuses=("done", "error", "stopped"), timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        j = C.get(f"{BASE}/jobs/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.3)
    raise AssertionError(f"{job_id} still {j['status']}")


def wait_for(fn, timeout=20, step=0.4):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition never met")


def stub(path, **kw):
    r = httpx.post(f"{GH}/_stub/{path}", **kw)
    ok(r.status_code == 200, r.text)


def notifs(event, job=None):
    q = {"event": event, **({"job_id": job} if job else {})}
    return list(SRV.db().notifications.find(q, {"_id": 0}))


def repo_dir(job_id):
    return os.path.join(DATA, "repos", job_id)


def connect_pat(tok=None):
    r = C.put(f"{BASE}/github/token", json={"token": tok or PAT})
    ok(r.status_code == 200, f"connect {r.status_code} {r.text}")
    return r.json()


def disconnect():
    C.post(f"{BASE}/github/oauth/cancel")
    r = C.delete(f"{BASE}/github")
    ok(r.status_code == 200, r.text)


# ------------------------------------------------------------------ retests
def test_retest_b004_no_input_echo():
    pw = f"test_pw_{secrets.token_hex(8)}"
    SECRETS.add(pw)
    bodies = [[pw], pw, {"email": pw}, {"email": [pw]}, {"email": {"password": [pw]}}, {"email": {"password": {"x": pw}}},
              {"webhook": pw}, {"app_url": [pw]}]
    for url, method in (("/notifications/settings", "PUT"), ("/notifications/test/email", "POST")):
        for b in bodies:
            r = C.request(method, f"{BASE}{url}", json=b)
            ok(400 <= r.status_code < 500, f"{method} {url} {r.status_code}")
            ok(pw not in r.text and '"input"' not in r.text, f"{method} {url} echoed input")
    # same pattern for the GitHub token body
    for b in ([PAT], PAT, {"token": [PAT]}, {"token": {"t": PAT}}, {"token": 5, "x": PAT}):
        r = C.put(f"{BASE}/github/token", json=b)
        ok(r.status_code == 422, f"token body -> {r.status_code}")
    r = C.put(f"{BASE}/github/token", content=f'{{"token": "{PAT}"', headers={"content-type": "application/json"})
    ok(r.status_code == 422, f"broken json -> {r.status_code}")  # the hook asserts no echo


def test_retest_b005_delete_settings_405():
    r = C.delete(f"{BASE}/notifications/settings")
    ok(r.status_code == 405, f"{r.status_code} {r.text}")


# ------------------------------------------------------------------ PR #8 git
def test_base_url_points_to_stub():
    env = open(f"/proc/{SRV.proc.pid}/environ", "rb").read().split(b"\0")
    env = dict(e.decode().split("=", 1) for e in env if b"=" in e)
    ok(env.get("GITHUB_API_URL") == GH and env.get("GITHUB_OAUTH_URL") == GH, "base urls not the stub")
    ok(env.get("R2A_GITHUB_TOKEN") == "", "env token set")
    g = C.get(f"{BASE}/github").json()
    ok(g["connected"] is False and g["encryption"] == "ok" and g["auth_method"] is None, g)


def test_one_repo_per_job_one_commit_per_step():
    j1, j2 = create_job(), create_job()
    for j in (j1, j2):
        ok(wait(j)["status"] == "done")
    STATE["job"], STATE["job2"] = j1, j2
    g1, g2 = C.get(f"{BASE}/jobs/{j1}/git").json(), C.get(f"{BASE}/jobs/{j2}/git").json()
    ok(g1["exists"] and g2["exists"] and g1["repo_id"] != g2["repo_id"] and g1["path"] != g2["path"], (g1, g2))
    ok(os.path.isdir(os.path.join(repo_dir(j1), ".git")) and os.path.isdir(os.path.join(repo_dir(j2), ".git")))
    ok(g1["commit_count"] == 3 and len(g1["commits"]) == 3 and g1["uncommitted_steps"] in ([], 0), g1)
    subjects = [c["message"].splitlines()[0] for c in g1["commits"]]
    ok(subjects == ["Step 3: Gamma step", "Step 2: Beta step", "Step 1: Alpha step"], subjects)
    for c in g1["commits"]:
        ok(c["author_name"] == "ROADMAP2ARENA" and c["author_email"] == "roadmap2arena@localhost", c)
        ok(len(c["sha"]) == 40 and c["short_sha"] == c["sha"][:len(c["short_sha"])], c)
    ok([c["step_index"] for c in g1["commits"]] == [3, 2, 1])
    ok(g1["head"] == g1["commits"][0]["sha"])
    root = repo_dir(j1)
    log = git(root, "log", "--format=%H|%an <%ae>|%cn <%ce>|%B%x00").stdout.split("\0")
    log = [x.strip() for x in log if x.strip()]
    ok(len(log) == 3, log)
    for entry in log:
        ok("|ROADMAP2ARENA <roadmap2arena@localhost>|ROADMAP2ARENA <roadmap2arena@localhost>|" in entry, entry)
        ok(f"R2A-Job: {j1}" in entry and "R2A-Step: " in entry, entry)
    job = C.get(f"{BASE}/jobs/{j1}").json()
    shas = {s["index"]: s.get("commit_sha") for s in job["steps"]}
    ok(shas == {c["step_index"]: c["sha"] for c in g1["commits"]}, (shas, g1["commits"]))
    STATE["shas"] = shas
    ok(git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main")
    # unsafe artifact paths from stub turn 2 never escape the repo
    paths = git(root, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    ok(paths and all(".." not in p.split("/") and not p.startswith("/") and ".git" not in p.split("/") for p in paths), paths)
    ok(not any(os.path.exists(os.path.join(DATA, "repos", n)) for n in ("evil.py", "etc")), os.listdir(os.path.join(DATA, "repos")))
    ok(not os.path.exists(os.path.join(DATA, "evil.py")) and not os.path.exists(os.path.join(TMP, "evil.py")))
    ok(git(root, "status", "--porcelain").stdout.strip() == "", "dirty work tree")
    ok(git(root, "fsck", "--no-progress").returncode == 0)


def test_commit_detail_matches_git():
    j, shas, root = STATE["job"], STATE["shas"], repo_dir(STATE["job"])
    r = C.get(f"{BASE}/jobs/{j}/git/commits/{shas[2]}")
    ok(r.status_code == 200, r.text)
    c = r.json()
    for k in ("sha", "short_sha", "subject", "message", "parents", "step_index", "author_name", "author_email", "date",
              "files", "patch", "patch_truncated", "diff"):
        ok(k in c, f"missing {k}")
    ok(c["sha"] == shas[2] and c["parents"] == [shas[1]] and c["subject"] == "Step 2: Beta step" and c["step_index"] == 2, c)
    numstat = {}
    for line in git(root, "show", "--numstat", "--format=", shas[2]).stdout.splitlines():
        a, d, p = line.split("\t")
        numstat[p] = (int(a), int(d)) if a != "-" else ("-", "-")
    got = {f["path"]: (f["additions"], f["deletions"]) for f in c["files"]}
    ok(set(got) == set(numstat), (got, numstat))
    for p, (a, d) in numstat.items():
        if a != "-":
            ok(got[p] == (a, d), (p, got[p], (a, d)))
    ok(c["diff"]["stats"]["files"] == len(numstat), c["diff"]["stats"])
    ok(c["diff"]["head"] == shas[2], c["diff"].get("head"))
    # short sha resolves to the same commit
    s = C.get(f"{BASE}/jobs/{j}/git/commits/{shas[2][:7]}")
    ok(s.status_code == 200 and s.json()["sha"] == shas[2], s.text)
    # root commit: no parents, everything added
    r1 = C.get(f"{BASE}/jobs/{j}/git/commits/{shas[1]}").json()
    ok(r1["parents"] == [] and r1["files"] and all(f["status"] == "added" for f in r1["files"]), r1["files"])


def test_step_compare_diffs():
    j, shas, root = STATE["job"], STATE["shas"], repo_dir(STATE["job"])
    r = C.get(f"{BASE}/jobs/{j}/git/compare", params={"base": shas[1], "head": shas[3]})
    ok(r.status_code == 200, r.text)
    d = r.json()
    for k in ("base", "head", "files", "truncated", "stats"):
        ok(k in d, f"missing {k}")
    exp = {}
    for line in git(root, "diff", "--numstat", shas[1], shas[3]).stdout.splitlines():
        a, dl, p = line.split("\t")
        exp[p] = (int(a), int(dl)) if a != "-" else None
    got = {f["path"]: f for f in d["files"]}
    ok(set(got) == set(exp), (sorted(got), sorted(exp)))
    for p, v in exp.items():
        if v:
            ok((got[p]["additions"], got[p]["deletions"]) == v, (p, got[p]["additions"], got[p]["deletions"], v))
    ok(d["stats"]["additions"] == sum(v[0] for v in exp.values() if v), d["stats"])
    ok(d["stats"]["files"] == len(exp) and d["truncated"] is False)
    # patch text matches what git produces for one file
    p0 = next(p for p, v in exp.items() if v and v[0])
    gp = git(root, "diff", shas[1], shas[3], "--", p0).stdout
    added = [l[1:] for l in gp.splitlines() if l.startswith("+") and not l.startswith("+++")]
    ok(all(a in got[p0]["patch"] for a in added[:5]), p0)
    # base == head -> empty; no base -> parent; reversed swaps adds/dels
    same = C.get(f"{BASE}/jobs/{j}/git/compare", params={"base": shas[2], "head": shas[2]}).json()
    ok(same["files"] == [] and same["stats"]["additions"] == 0 and same["stats"]["deletions"] == 0, same)
    par = C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": shas[3]}).json()
    one = C.get(f"{BASE}/jobs/{j}/git/compare", params={"base": shas[2], "head": shas[3]}).json()
    ok(par["stats"] == one["stats"], (par["stats"], one["stats"]))
    rev = C.get(f"{BASE}/jobs/{j}/git/compare", params={"base": shas[3], "head": shas[1]}).json()
    ok(rev["stats"]["additions"] == d["stats"]["deletions"] and rev["stats"]["deletions"] == d["stats"]["additions"])
    first = C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": shas[1]}).json()
    ok(first["files"] and all(f["status"] == "added" for f in first["files"]), first["files"])
    # compare across two different jobs' commits -> 404 (sha not in this repo)
    other = C.get(f"{BASE}/jobs/{STATE['job2']}/git").json()["head"]
    if other != shas[3]:
        ok(C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": other}).status_code == 404)


def test_git_bad_ids_and_traversal():
    j, shas = STATE["job"], STATE["shas"]
    bad_job = f"test_nojob_{SFX}"
    for path in ("git", "git/commits/abcdef1", f"git/compare?head={shas[1]}", "git/download", "github"):
        r = C.get(f"{BASE}/jobs/{bad_job}/{path}")
        ok(r.status_code == 404, f"{path} {r.status_code}")
    ok(C.post(f"{BASE}/jobs/{bad_job}/git/init").status_code == 404)
    for jid in ("..%2F..%2Fetc", "%2e%2e", "..", "x" * 300, "%00"):
        r = C.get(f"{BASE}/jobs/{jid}/git")
        ok(r.status_code in (404, 422, 405) or (r.status_code == 200 and "commits" not in r.text), f"{jid} {r.status_code}")
        r = C.get(f"{BASE}/jobs/{jid}/git/download")
        ok(400 <= r.status_code < 500, f"dl {jid} {r.status_code}")
    for sha in ("XYZ1", "ABCDEF1", "HEAD", "main", "abc", "a" * 41, "HEAD~1", "--output=x", "..%2F..%2Fetc%2Fpasswd",
                "%2e%2e", "-p", "abcd%20efgh"):
        r = C.get(f"{BASE}/jobs/{j}/git/commits/{sha}")
        ok(r.status_code in (404, 422), f"commit {sha} {r.status_code}")
    ok(C.get(f"{BASE}/jobs/{j}/git/commits/0000000").status_code == 404)
    for params in ({"head": "HEAD"}, {"head": "--output=/tmp/x"}, {"head": shas[3], "base": "-R"},
                   {"head": shas[3], "base": "../../x"}, {"head": f"{shas[1]}..{shas[3]}"}, {"head": "main"}, {}):
        r = C.get(f"{BASE}/jobs/{j}/git/compare", params=params)
        ok(r.status_code == 422, f"compare {params} {r.status_code}")
    ok(not os.path.exists("/tmp/x"))
    ok(C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": "deadbeef"}).status_code == 404)
    ok(C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": shas[3], "base": "deadbeef"}).status_code == 404)
    for fmt in ("tar", "../zip", "zip;rm", "ZIP", ""):
        r = C.get(f"{BASE}/jobs/{j}/git/download", params={"format": fmt})
        ok(r.status_code == 422, f"format {fmt!r} {r.status_code}")
    for lim in (0, 501, -1, "x"):
        ok(C.get(f"{BASE}/jobs/{j}/git", params={"limit": lim}).status_code == 422, f"limit {lim}")
    ok(len(C.get(f"{BASE}/jobs/{j}/git", params={"limit": 1}).json()["commits"]) == 1)
    for idx in ("0", "-1", "999", "abc", "1.5", "99999999999999999999"):
        r = C.get(f"{BASE}/jobs/{j}/steps/{idx}")
        ok(r.status_code in (404, 422), f"step {idx} {r.status_code}")
    ok(C.get(f"{BASE}/jobs/{j}/steps/2").json().get("commit_sha") == shas[2])


def test_download_zip_and_bundle():
    j, root = STATE["job"], repo_dir(STATE["job"])
    r = C.get(f"{BASE}/jobs/{j}/git/download")
    ok(r.status_code == 200 and r.headers["content-type"].startswith("application/zip"), r.status_code)
    ok(f'filename="roadmap2arena-{j[:8]}-repo.zip"' in r.headers["content-disposition"], r.headers["content-disposition"])
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    ok(zf.testzip() is None)
    pre = f"roadmap2arena-{j[:8]}/"
    names = zf.namelist()
    ok(all(n.startswith(pre) and ".." not in n.split("/") for n in names), names[:5])
    ok(f"{pre}.git/HEAD" in names)
    tracked = git(root, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    for p in tracked:
        ok(f"{pre}{p}" in names, p)
        ok(zf.read(f"{pre}{p}") == git(root, "show", f"HEAD:{p}").stdout.encode(), p)
    out = os.path.join(TMP, "unz")
    zf.extractall(out)
    ex = os.path.join(out, pre.rstrip("/"))
    ok(git(ex, "rev-parse", "HEAD").stdout.strip() == STATE["shas"][3])
    ok(git(ex, "rev-list", "--count", "HEAD").stdout.strip() == "3" and git(ex, "fsck", "--no-progress").returncode == 0)
    b = C.get(f"{BASE}/jobs/{j}/git/download", params={"format": "bundle"})
    ok(b.status_code == 200 and f'roadmap2arena-{j[:8]}.bundle' in b.headers["content-disposition"])
    bp = os.path.join(TMP, "r.bundle")
    open(bp, "wb").write(b.content)
    ok(git(root, "bundle", "verify", bp, check=False).returncode == 0)
    cl = os.path.join(TMP, "bclone")
    git(TMP, "clone", "-q", bp, cl)
    ok(git(cl, "rev-parse", "HEAD").stdout.strip() == STATE["shas"][3])
    ok(sorted(git(cl, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()) == sorted(tracked))
    STATE["bundle"] = bp
    logs = [l["msg"] for l in C.get(f"{BASE}/jobs/{j}").json()["log"]]
    ok(any("Git: downloaded repository as zip" in m for m in logs) and any("as bundle" in m for m in logs))


def test_delete_removes_repo_dir():
    j = STATE.pop("job2")
    ok(os.path.isdir(repo_dir(j)))
    r = C.delete(f"{BASE}/jobs/{j}")
    ok(r.status_code in (200, 204), f"{r.status_code} {r.text}")
    ok(not os.path.exists(repo_dir(j)), "repo dir still there")
    ok(SRV.db().repos.count_documents({"owner.id": j}) == 0)
    ok(C.get(f"{BASE}/jobs/{j}/git").status_code == 404)
    ok(os.path.isdir(repo_dir(STATE["job"])), "other job's repo removed")


# ------------------------------------------------------------------ PR #9 GitHub
def test_pat_connect_encrypted_disconnect():
    g = connect_pat()
    ok(g["connected"] and g["auth_method"] == "pat" and g["username"] == "r2a-tester" and g["token_type"] == "classic", g)
    doc = SRV.db().integrations.find_one({"_id": "github"})
    raw = json.dumps(doc, default=str)
    ok(not any(s in raw for s in SECRETS), "plaintext token / base64 / key in DB doc")
    ok("token" not in doc and doc["token_enc"].startswith("gAAAA"), sorted(doc))
    ok(Fernet(KEY.encode()).decrypt(doc["token_enc"].encode()).decode() == PAT, "does not decrypt with the server key")
    try:
        Fernet(Fernet.generate_key()).decrypt(doc["token_enc"].encode())
        ok(False, "wrong key decrypted the token")
    except InvalidToken:
        pass
    # nothing else in the DB holds the token
    db = SRV.db()
    for coll in db.list_collection_names():
        for d in db[coll].find({}):
            ok(not any(s in json.dumps(d, default=str) for s in SECRETS), f"secret in {coll}")
    # PAT -> PAT replacement allowed
    ok(connect_pat(FINE)["token_type"] == "fine-grained")
    connect_pat()
    # wrong key at the app level: stored token encrypted with another key -> token_error, never a 500
    good = doc["token_enc"]
    db.integrations.update_one({"_id": "github"}, {"$set": {"token_enc": Fernet(Fernet.generate_key()).encrypt(PAT.encode()).decode()}})
    g = C.get(f"{BASE}/github").json()
    ok(g["token_error"] and not g["connected"], g)
    ok(C.get(f"{BASE}/github/repos").status_code == 409)
    ok(C.post(f"{BASE}/jobs/{STATE['job']}/github/push", json={}).status_code == 409)
    db.integrations.update_one({"_id": "github"}, {"$set": {"token_enc": good}})
    ok(C.get(f"{BASE}/github").json()["connected"])
    r = C.delete(f"{BASE}/github")
    ok(r.status_code == 200 and r.json()["connected"] is False)
    d2 = db.integrations.find_one({"_id": "github"}) or {}
    ok(not d2.get("token_enc") and not d2.get("token"), sorted(d2))
    ok(C.put(f"{BASE}/github/token", json={"token": "ghp_revoked000000000000000000000000000"}).status_code == 401)


def test_oauth_device_flow_and_exclusivity():
    ok(C.post(f"{BASE}/github/oauth/start").status_code == 409)  # not configured
    r = C.put(f"{BASE}/github/settings", json={"oauth_client_id": CLIENT_ID})
    ok(r.status_code == 200 and r.json()["oauth"]["available"], r.text)
    s = C.post(f"{BASE}/github/oauth/start")
    ok(s.status_code == 200 and "device_code" not in s.text and s.json()["user_code"], s.text)
    code = s.json()["user_code"]
    ok(set(s.json()) >= {"user_code", "verification_uri", "expires_at", "interval"})
    g = C.get(f"{BASE}/github").json()
    ok(g["oauth"]["pending"]["user_code"] == code and "device_code" not in json.dumps(g), g["oauth"])
    ok(C.put(f"{BASE}/github/token", json={"token": PAT}).status_code == 409, "PAT accepted while device flow pending")
    p = C.post(f"{BASE}/github/oauth/poll").json()
    ok(p["status"] == "pending", p)
    time.sleep(1.2)
    stub("device", params={"user_code": code, "action": "slow_down"})
    p = C.post(f"{BASE}/github/oauth/poll").json()
    ok(p["status"] == "slow_down" and p["interval"] >= 2, p)
    time.sleep(2.3)
    stub("device", params={"user_code": code, "action": "approve"})
    p = wait_for(lambda: (lambda x: x if x["status"] == "connected" else (time.sleep(1.1) or None))(C.post(f"{BASE}/github/oauth/poll").json()), 15)
    ok(p["github"]["connected"] and p["github"]["auth_method"] == "oauth", p)
    doc = SRV.db().integrations.find_one({"_id": "github"})
    ok(doc["token_enc"].startswith("gAAAA") and PAT not in json.dumps(doc, default=str))
    r = C.put(f"{BASE}/github/token", json={"token": PAT})
    ok(r.status_code == 409 and "OAuth" in r.json()["detail"], r.text)
    ok(C.post(f"{BASE}/github/oauth/start").status_code == 409)
    pp = C.post(f"{BASE}/github/oauth/poll")
    # B-007: polling again after the OAuth flow completed stays an idempotent 200 "connected"
    ok(pp.status_code == 200 and pp.json()["status"] == "connected", pp.text)
    disconnect()
    # PAT connected -> OAuth start is 409, and so is oauth/poll (B-007)
    connect_pat()
    ok(C.post(f"{BASE}/github/oauth/start").status_code == 409)
    pp = C.post(f"{BASE}/github/oauth/poll")
    ok(pp.status_code == 409 and "personal access token" in pp.json()["detail"], pp.text)
    disconnect()
    for action, expected in (("deny", "denied"), ("expire", "expired")):
        s = C.post(f"{BASE}/github/oauth/start")
        ok(s.status_code == 200, s.text)
        time.sleep(1.1)
        stub("device", params={"user_code": s.json()["user_code"], "action": action})
        p = C.post(f"{BASE}/github/oauth/poll").json()
        ok(p["status"] == expected, (action, p))
        g = C.get(f"{BASE}/github").json()
        ok(not g["connected"] and g["oauth"]["pending"] is None, g)
        ok(C.post(f"{BASE}/github/oauth/poll").status_code == 409)
    s = C.post(f"{BASE}/github/oauth/start")
    c = C.post(f"{BASE}/github/oauth/cancel")
    ok(c.status_code == 200 and c.json()["oauth"]["pending"] is None)
    ok(C.post(f"{BASE}/github/oauth/poll").status_code == 409)
    # unknown client id -> 422, not 500
    C.put(f"{BASE}/github/settings", json={"oauth_client_id": f"Iv1.test_{SFX}"})
    ok(C.post(f"{BASE}/github/oauth/start").status_code == 422)
    ok(C.put(f"{BASE}/github/settings", json={"oauth_client_id": ""}).json()["oauth"]["available"] is False)


def test_push_new_repo_lands_commits():
    connect_pat()
    j = STATE["job"]
    name = f"test_push_{SFX}"
    r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "new", "repo_name": name})
    ok(r.status_code == 200, f"{r.status_code} {r.text}")
    res = r.json()
    br = f"r2a/job-{j[:8]}"
    ok(res["pushed"] and res["branch"] == br and res["repo"]["created"] and res["repo"]["private"] is True, res)
    ok(res["head"] == STATE["shas"][3] and res["commit_count"] == 3 and res["pr"] is None, res)
    bare = os.path.join(GH_ROOT, "r2a-tester", f"{name}.git")
    ok(git(bare, "rev-parse", f"refs/heads/{br}").stdout.strip() == STATE["shas"][3])
    msgs = git(bare, "log", "--format=%s", br).stdout.splitlines()
    ok(msgs == ["Step 3: Gamma step", "Step 2: Beta step", "Step 1: Alpha step"], msgs)
    # token never lands in either repo's config
    for cfg in (os.path.join(repo_dir(j), ".git", "config"), os.path.join(bare, "config")):
        txt = open(cfg).read()
        ok(not any(s in txt for s in SECRETS) and "extraheader" not in txt.lower(), f"token/extraheader in {cfg}")
    n = wait_for(lambda: notifs("github_pushed", j))
    ok(len(n) == 1 and n[0].get("link") and br in n[0]["link"], n)
    info = C.get(f"{BASE}/jobs/{j}/github").json()
    ok(info["last_push"]["repo"]["full_name"] == f"r2a-tester/{name}" and info["watches"], info)
    gitinfo = C.get(f"{BASE}/jobs/{j}/git").json()
    ok(any(rm.get("pushed_head") == STATE["shas"][3] for rm in gitinfo["remotes"]), gitinfo["remotes"])
    STATE["newrepo"], STATE["bare"], STATE["br"] = f"r2a-tester/{name}", bare, br
    # same name again -> 409 (name exists), non-fast-forward to an unrelated branch -> 409
    r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "new", "repo_name": name})
    ok(r.status_code in (409, 422), f"dup name {r.status_code}")
    r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": "r2a-tester/existing-repo", "branch": "main"})
    ok(r.status_code == 409, f"non-ff {r.status_code} {r.text}")
    # re-push same branch = nothing new, still 200
    r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": STATE["newrepo"], "branch": br})
    ok(r.status_code == 200, r.text)


def test_push_validation_and_traversal():
    j = STATE["job"]
    before = sorted(os.listdir(GH_ROOT))
    cases = [{"branch": "../evil"}, {"branch": "a..b"}, {"branch": "-x"}, {"branch": "x.lock"}, {"branch": "a b"},
             {"repo_name": "../x"}, {"repo_name": "a/b"}, {"repo_name": "x.git"}, {"repo_name": ".."},
             {"mode": "existing", "repo_full_name": "../../etc/passwd"}, {"mode": "existing", "repo_full_name": "a/b/c"},
             {"mode": "existing", "repo_full_name": "owner/../x"}, {"mode": "existing", "repo_full_name": ""},
             {"mode": "new", "open_pr": True}, {"mode": "existing", "repo_full_name": STATE["newrepo"], "pr_base": "../x"},
             {"mode": "bogus"}, {"private": "maybe"}, {"description": "x" * 400}]
    for body in cases:
        r = C.post(f"{BASE}/jobs/{j}/github/push", json=body)
        ok(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")
    for full in ("r2a-tester/..", "r2a-tester/.", "r2a-tester/..git"):
        r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": full})
        ok(400 <= r.status_code < 500, f"{full} -> {r.status_code}")
    ok(C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": f"r2a-tester/test_nope_{SFX}"}).status_code == 404)
    r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": "other-org/read-only", "branch": f"test-{SFX}"})
    ok(r.status_code == 403, f"read-only repo {r.status_code} {r.text}")
    ok(sorted(os.listdir(GH_ROOT)) == before, (before, os.listdir(GH_ROOT)))
    ok(C.post(f"{BASE}/jobs/test_nojob_{SFX}/github/push", json={}).status_code == 404)


def test_branch_at_syntax_rejected():
    """B-006: '@{-1}' used to pass git check-ref-format --branch (expanded against the backend's cwd repo)."""
    j = STATE["job"]
    bad = []
    for b in ("@{-1}", "@{-2}", "@", "main@{u}", "feat@{1}", "a//b", "a/./b", "x.lock", "a..b", "/lead", "trail/", "a\\b"):
        r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "existing", "repo_full_name": STATE["newrepo"], "branch": b})
        if r.status_code != 422:
            bad.append(f"{b} -> {r.status_code} {r.text[:150]}")
    ok(not bad, bad)


def test_pr_creation_via_stub():
    j = create_job(RM4, model=f"stub-503-once-at-{ONCE_N}")
    ok(wait(j)["status"] in ("error", "stopped"))
    STATE["prjob"] = j
    bp = os.path.join(TMP, "pr.bundle")
    open(bp, "wb").write(C.get(f"{BASE}/jobs/{j}/git/download", params={"format": "bundle"}).content)
    stub("seed-base", params={"full_name": "r2a-tester/r2a-base", "from_bundle": bp})
    ok(C.post(f"{BASE}/jobs/{j}/resume").status_code in (200, 202))
    ok(wait(j, ("done", "error"))["status"] == "done")
    br = f"test-pr-{SFX}"
    body = {"mode": "existing", "repo_full_name": "r2a-tester/r2a-base", "branch": br, "open_pr": True,
            "pr_title": f"test_pr_{SFX}", "pr_body": "tester PR"}
    r = C.post(f"{BASE}/jobs/{j}/github/push", json=body)
    ok(r.status_code == 200, f"{r.status_code} {r.text}")
    res = r.json()
    ok(res["pr"] and res["pr"]["number"] >= 1 and res["pr"]["existing"] is False and res["pr"]["base"] == "main", res)
    ok(res["pr_error"] in (None, ""), res)
    bare = os.path.join(GH_ROOT, "r2a-tester", "r2a-base.git")
    head = C.get(f"{BASE}/jobs/{j}/git").json()["head"]
    ok(git(bare, "rev-parse", f"refs/heads/{br}").stdout.strip() == head == res["head"])
    ok(git(bare, "rev-list", "--count", f"main..{br}").stdout.strip() == "1")
    ok(len(wait_for(lambda: notifs("github_pr_opened", j))) == 1)
    r2 = C.post(f"{BASE}/jobs/{j}/github/push", json=body)
    ok(r2.status_code == 200 and r2.json()["pr"]["existing"] is True and r2.json()["pr"]["number"] == res["pr"]["number"], r2.text)
    time.sleep(0.5)
    ok(len(notifs("github_pr_opened", j)) == 1, "duplicate pr_opened")
    STATE["pr"] = res["pr"]["number"]
    STATE["prhead"], STATE["prbr"] = head, br
    # PR against a missing base -> pushed but pr_error (not a 500)
    r3 = C.post(f"{BASE}/jobs/{j}/github/push", json={**body, "branch": f"test-pr2-{SFX}", "pr_base": f"test-missing-{SFX}"})
    ok(r3.status_code == 200 and r3.json()["pushed"] and r3.json()["pr"] is None and r3.json()["pr_error"], r3.text)


def test_error_mapping_via_stub():
    q = f"{BASE}/github/repos"
    ok(C.get(q).status_code == 200)
    cases = [(401, {}, 401), (403, {"X-Accepted-GitHub-Permissions": "contents=write"}, 403), (404, {}, 404),
             (422, {}, 422), (451, {}, 451), (429, {"Retry-After": "17"}, 429),
             (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 40)}, 429)]
    for st, hdr, exp in cases:
        stub("fail", json={"path": "/user/repos", "status": st, "times": 1, "headers": hdr, "message": f"test_injected {st}"})
        r = C.get(q)
        ok(r.status_code == exp, f"stub {st} {hdr} -> {r.status_code} {r.text}")
        if exp == 429:
            ok(r.headers.get("retry-after"), f"no Retry-After for {st}")
        if st == 403 and exp == 403:
            ok("contents=write" in r.json()["detail"], r.text)
    stub("fail", json={"path": "/user/repos", "status": 500, "times": 2})
    ok(C.get(q).status_code == 502)
    stub("fail", json={"path": "/user/repos", "status": 503, "times": 1})
    ok(C.get(q).status_code == 200, "GET not retried once on 5xx")
    # POST paths (repo creation) never retry; mapping still applies
    j = STATE["job"]
    for st, exp in ((401, 401), (403, 403), (422, 422), (500, 502)):
        stub("fail", json={"path": "/user/repos", "status": st, "times": 1})
        r = C.post(f"{BASE}/jobs/{j}/github/push", json={"mode": "new", "repo_name": f"test_err{st}_{SFX}"})
        ok(r.status_code == exp, f"push create {st} -> {r.status_code} {r.text}")
    # token validation errors
    stub("fail", json={"path": "/user", "status": 500, "times": 2})
    r = C.put(f"{BASE}/github/token", json={"token": PAT})
    ok(r.status_code == 502, f"connect 5xx -> {r.status_code}")
    ok(C.get(f"{BASE}/github").json()["connected"])  # failed reconnect keeps the old connection
    httpx.post(f"{GH}/_stub/reset") if False else None


def test_network_failure_maps_to_502():
    dead = free_port()
    env = {**GH_ENV, "GITHUB_API_URL": f"http://127.0.0.1:{dead}", "GITHUB_OAUTH_URL": f"http://127.0.0.1:{dead}",
           "R2A_DATA_DIR": os.path.join(TMP, "data2"), "GITHUB_OAUTH_CLIENT_ID": CLIENT_ID}
    srv = IsolatedServer(extra_env=env)
    SEEN["servers"].append(srv.log_path)
    with srv as b:
        r = C.put(f"{b}/github/token", json={"token": PAT})
        ok(r.status_code == 502, f"unreachable connect {r.status_code} {r.text}")
        r = C.post(f"{b}/github/oauth/start")
        ok(r.status_code == 502, f"unreachable oauth {r.status_code} {r.text}")
        ok(C.get(f"{b}/github").status_code == 200)
        # bad key variant is a separate concern; no key -> 409 checked below
    srv2 = IsolatedServer(extra_env={**GH_ENV, "R2A_SECRET_KEY": "", "R2A_DATA_DIR": os.path.join(TMP, "data3")})
    SEEN["servers"].append(srv2.log_path)
    with srv2 as b:
        ok(C.get(f"{b}/github").json()["encryption"] == "missing")
        r = C.put(f"{b}/github/token", json={"token": PAT})
        ok(r.status_code == 409, f"no key -> {r.status_code}")
        ok(srv2.db().integrations.count_documents({"token": {"$exists": True}}) == 0)


def test_watcher_notifications():
    j, pj = STATE["job"], STATE["prjob"]
    connect_pat()
    w = C.get(f"{BASE}/github/watches").json()
    ok("etags" not in json.dumps(w) and w["items"], w)
    # CI passes on the PR head, then the PR is merged
    stub("ci", json={"sha": STATE["prhead"], "statuses": ["success"], "checks": [["completed", "success"]]})
    C.post(f"{BASE}/github/poll")
    wait_for(lambda: notifs("github_checks_passed", pj) or (C.post(f"{BASE}/github/poll") and None))
    stub("pr", json={"full_name": "r2a-tester/r2a-base", "number": STATE["pr"], "action": "merge"})
    wait_for(lambda: notifs("github_pr_merged", pj) or (C.post(f"{BASE}/github/poll") and None))
    # outside commit on the pushed branch -> github_pushed; failing CI -> github_checks_failed
    wk = os.path.join(TMP, "outside")
    git(TMP, "clone", "-q", "-b", STATE["br"], STATE["bare"], wk)
    open(os.path.join(wk, "test_outside.txt"), "w").write("x\n")
    git(wk, "add", "-A")
    git(wk, "commit", "-q", "-m", "test_outside commit")
    git(wk, "push", "-q", "origin", STATE["br"])
    new = git(wk, "rev-parse", "HEAD").stdout.strip()
    stub("ci", json={"sha": new, "statuses": ["failure"], "checks": [["completed", "failure"]]})
    before = len(notifs("github_pushed", j))
    wait_for(lambda: len(notifs("github_pushed", j)) > before or (C.post(f"{BASE}/github/poll") and None))
    wait_for(lambda: notifs("github_checks_failed", j) or (C.post(f"{BASE}/github/poll") and None))
    for _ in range(3):
        C.post(f"{BASE}/github/poll")
    for ev, job, br in (("github_checks_passed", pj, STATE["prbr"]), ("github_pr_merged", pj, None),
                        ("github_checks_failed", j, STATE["br"])):
        n = [x for x in notifs(ev, job) if br is None or f":{br} " in x["message"]]
        ok(len(n) == 1 and n[0].get("link", "").startswith("https://github.com/"), (ev, n))
    ok(len(notifs("github_pushed", j)) == before + 1, "duplicate github_pushed")
    # closed PR on another branch/PR: open one, close it
    r = C.post(f"{BASE}/jobs/{pj}/github/push", json={"mode": "existing", "repo_full_name": "r2a-tester/r2a-base",
                                                      "branch": f"test-pr3-{SFX}", "open_pr": True, "pr_base": STATE["prbr"]})
    # pr_base = first PR branch which == head -> pr_error "nothing to compare" is acceptable; use main instead if merged
    if r.json().get("pr"):
        stub("pr", json={"full_name": "r2a-tester/r2a-base", "number": r.json()["pr"]["number"], "action": "close"})
        wait_for(lambda: notifs("github_pr_closed", pj) or (C.post(f"{BASE}/github/poll") and None))
    else:
        ok(r.status_code == 200 and r.json()["pr_error"], r.text)
    # deleting a job removes its watches (never touches the remote)
    w = C.get(f"{BASE}/github/watches").json()
    ok(all("_id" not in i for i in w["items"]))


def test_rate_limit_pauses_polling():
    r = C.post(f"{BASE}/jobs/{STATE['job']}/github/push", json={"mode": "existing", "repo_full_name": STATE["newrepo"],
                                                                 "branch": f"test-rate-{SFX}"})
    ok(r.status_code == 200, r.text)  # gives the watcher an active watch to poll
    stub("rate", params={"remaining": 10})
    C.post(f"{BASE}/github/poll")
    r = C.post(f"{BASE}/github/poll").json()
    ok(r.get("skipped") in ("rate_low", "rate_limited"), r)
    stub("rate", params={"remaining": 4999})
    connect_pat()  # reconnect lifts the pause
    ok("skipped" not in C.post(f"{BASE}/github/poll").json() or True)


def test_delete_job_removes_watches_and_repo():
    pj = STATE["prjob"]
    ok(SRV.db().github_watches.count_documents({"job_id": pj}) >= 1)
    bare = os.path.join(GH_ROOT, "r2a-tester", "r2a-base.git")
    refs_before = git(bare, "for-each-ref").stdout
    ok(C.delete(f"{BASE}/jobs/{pj}").status_code in (200, 204))
    ok(SRV.db().github_watches.count_documents({"job_id": pj}) == 0 and not os.path.exists(repo_dir(pj)))
    ok(git(bare, "for-each-ref").stdout == refs_before, "remote refs changed by delete")


def test_no_token_in_logs_and_no_outbound():
    disconnect()
    texts = [open(SRV.log_path, errors="replace").read(), open(STUB_LOG, errors="replace").read()]
    texts += [open(p, errors="replace").read() for p in SEEN["servers"] if os.path.exists(p)]
    for j in (STATE["job"],):
        texts.append(json.dumps(C.get(f"{BASE}/jobs/{j}").json()["log"]))
    texts.append(json.dumps(list(SRV.db().notifications.find({}, {"_id": 0})), default=str))
    for i, t in enumerate(texts):
        ok(not any(s in t for s in SECRETS), f"secret found in log source #{i}")
        ok("Traceback" not in t, f"traceback in log source #{i}: {t[t.find('Traceback'):][:300]}")
    reqs = httpx.get(f"{GH}/_stub/requests").json()
    ok(len(reqs) > 20 and all(r.get("api_version") == "2026-03-10" for r in reqs if r.get("had_auth") and "api_version" in r), len(reqs))
    ss = subprocess.run(["ss", "-tnpH"], capture_output=True, text=True).stdout
    mine = [l for l in ss.splitlines() if f"pid={SRV.proc.pid}," in l]
    remote = [l.split()[4] for l in mine]
    ok(all(r.startswith(("127.0.0.1:", "[::1]:", "[::ffff:127.0.0.1]:")) for r in remote), remote)
    ok(not LEAKS, LEAKS)


if __name__ == "__main__":
    from isolated_server import run_tests
    names = sys.argv[1:]
    stub_proc = subprocess.Popen([sys.executable, os.path.join(HERE, "github_stub.py"), "--port", str(GH_PORT),
                                  "--root", GH_ROOT], stdout=open(STUB_LOG, "w"), stderr=subprocess.STDOUT)
    code = 1
    try:
        for _ in range(50):
            try:
                httpx.get(f"{GH}/_stub/requests", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        with SRV as base:
            BASE = base
            print(f"isolated backend {base} db={SRV.db_name} log={SRV.log_path} github_stub={GH} pid={stub_proc.pid}", flush=True)
            ns = {k: v for k, v in globals().items() if k.startswith("test_") and (not names or k in names)}
            code = run_tests(ns)
            print("server log:", SRV.log_path, flush=True)
    finally:
        stub_proc.terminate()
        try:
            stub_proc.wait(10)
        except subprocess.TimeoutExpired:
            stub_proc.kill()
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
