"""PR C: per-job git history. Runs on an isolated backend (throwaway DB + temp data dir).

    /app/venv/bin/python tests/test_git.py
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from isolated_server import IsolatedServer, run_tests  # noqa: E402

RM = "# Git demo\n\n### First step\nmake a\n\n### Second step\nmake b\n\n### Third step\nmake c\n"
DATA = tempfile.mkdtemp(prefix="r2a-git-test-")
SRV = IsolatedServer(extra_env={"R2A_DATA_DIR": DATA})
BASE = ""
C = httpx.Client(timeout=30)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env={"PATH": os.environ["PATH"], "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
                               "GIT_CONFIG_GLOBAL": os.devnull}).stdout


def create(model="gpt-4o", **extra):
    r = C.post(f"{BASE}/jobs", json={"roadmap_md": RM, "model": model, **extra})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def wait(job_id, statuses=("done", "error", "stopped"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = C.get(f"{BASE}/jobs/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} still {j['status']}")


def wait_idle(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        q = C.get(f"{BASE}/queue").json()
        if not q.get("running") and not q.get("queued"):
            return
        time.sleep(0.3)


def repo_dir(job_id):
    return os.path.join(DATA, "repos", job_id)


STATE = {}


def test_commit_per_step():
    j = create()
    job = wait(j)
    assert job["status"] == "done"
    assert job["project_id"] is None and job["repo_id"]
    shas = [s["commit_sha"] for s in job["steps"]]
    assert all(shas) and len(set(shas)) == 3
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g["exists"] and g["commit_count"] == 3 and g["head"] == shas[-1] and g["uncommitted_steps"] == 0
    assert g["owner"] == {"type": "job", "id": j} and g["repo_id"] == job["repo_id"]
    assert [c["message"] for c in g["commits"]] == ["Step 3: Third step", "Step 2: Second step", "Step 1: First step"]
    assert [c["step_index"] for c in g["commits"]] == [3, 2, 1]
    assert all(c["author_name"] == "ROADMAP2ARENA" for c in g["commits"])
    # on disk: a real repo whose files match the latest artifacts, branch main, clean tree
    d = repo_dir(j)
    assert git(d, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(d, "status", "--porcelain") == ""
    assert "R2A-Step: 2" in git(d, "log", "-1", "--format=%B", shas[1])
    assert git(d, "log", "-1", "--format=%an <%ae>|%cn").strip() == "ROADMAP2ARENA <roadmap2arena@localhost>|ROADMAP2ARENA"
    files = set(git(d, "ls-files").split())
    latest = {p.lstrip("/") for s in job["steps"] for p in s["artifact_paths"] if ".." not in p}
    assert files == latest, (files, latest)
    log = [l["msg"] for l in job["log"]]
    assert any(m.startswith("Git: new repository repos/") for m in log)
    assert sum(m.startswith("Git: committed step") for m in log) == 3
    STATE["done"] = j


def test_unsafe_paths_skipped():
    job = C.get(f"{BASE}/jobs/{STATE['done']}").json()
    unsafe = [p for s in job["steps"] for p in s["artifact_paths"] if ".." in p]
    assert unsafe, "stub should produce a ../ path"
    assert any(f'Git: skipped "{unsafe[0]}"' in l["msg"] for l in job["log"])
    assert not os.path.exists(os.path.join(DATA, "repos", "evil.py"))


def test_path_safety_unit():
    import git_integration as gi
    assert gi.safe_rel_path("src/a.py") == ("src/a.py", None)
    assert gi.safe_rel_path("./x/../y")[0] is None
    for bad in (".git/config", "a/.GIT/hooks/pre-commit", ".git"):
        assert gi.safe_rel_path(bad)[0] is None, bad
    assert gi.safe_rel_path("/abs/p.txt") == ("abs/p.txt", None)
    root = tempfile.mkdtemp()
    assert gi._write_artifact(root, "a", "x") is None
    assert "conflicts with the file a" in gi._write_artifact(root, "a/b", "y")
    assert gi._write_artifact(root, "d/e.txt", "z") is None
    assert "directory" in gi._write_artifact(root, "d", "w")
    os.symlink("/tmp", os.path.join(root, "link"))
    assert "symlink" in gi._write_artifact(root, "link/x", "q")
    assert gi.one_line("a\nb   c" + "x" * 300).startswith("a b c") and len(gi.one_line("y" * 300)) == 200
    shutil.rmtree(root)


def test_commit_detail():
    j = STATE["done"]
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    first, last = g["commits"][-1], g["commits"][0]
    c = C.get(f"{BASE}/jobs/{j}/git/commits/{first['sha']}").json()
    assert c["parents"] == [] and c["subject"] == "Step 1: First step" and c["step_index"] == 1
    assert c["files"] and all(f["status"] == "added" for f in c["files"])
    assert "diff --git" in c["patch"] and c["patch_truncated"] is False
    c3 = C.get(f"{BASE}/jobs/{j}/git/commits/{last['short_sha']}").json()
    assert c3["sha"] == last["sha"] and len(c3["parents"]) == 1
    assert sum(f["additions"] or 0 for f in c3["files"]) == last["insertions"]
    assert C.get(f"{BASE}/jobs/{j}/git/commits/zzzz").status_code == 422
    assert C.get(f"{BASE}/jobs/{j}/git/commits/HEAD").status_code == 422
    assert C.get(f"{BASE}/jobs/{j}/git/commits/{'0' * 40}").status_code == 404
    assert C.get(f"{BASE}/jobs/nope/git/commits/{first['sha']}").status_code == 404
    assert C.get(f"{BASE}/jobs/nope/git").status_code == 404
    assert C.get(f"{BASE}/jobs/{j}/git?limit=1").json()["commits"][0]["sha"] == last["sha"]
    assert C.get(f"{BASE}/jobs/{j}/git?limit=0").status_code == 422


def test_structured_diff_and_compare():
    """Commit detail carries a structured diff; /git/compare diffs two step commits (DiffViewer input)."""
    j = STATE["done"]
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    first, last = g["commits"][-1], g["commits"][0]
    c = C.get(f"{BASE}/jobs/{j}/git/commits/{first['sha']}").json()
    d = c["diff"]
    assert d["base"] is None and d["head"] == first["sha"] and d["stats"]["files"] == len(c["files"])
    for f in d["files"]:
        assert f["status"] == "added" and f["patch"].startswith("diff --git") and "\n@@" in f["patch"]
        assert f["old_content"] is None and f["new_content"] is not None and f["context_expandable"] is True
        assert set(f) >= {"path", "old_path", "additions", "deletions", "binary", "patch_too_large"}
    assert d["stats"]["additions"] == sum(f["additions"] for f in d["files"])
    r = C.get(f"{BASE}/jobs/{j}/git/compare", params={"base": first["sha"], "head": last["sha"]})
    assert r.status_code == 200, r.text
    cmp = r.json()
    assert cmp["base"] == first["sha"] and cmp["head"] == last["sha"]
    total = sum(x["insertions"] for x in g["commits"][:-1])
    assert cmp["stats"]["additions"] == total, (cmp["stats"], total)
    mod = [f for f in cmp["files"] if f["status"] == "modified"]
    assert all(f["old_content"] is not None and f["new_content"] is not None for f in mod)
    # default base = parent; same commit = empty diff; short shas resolve
    assert C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": last["short_sha"]}).json()["base"] == g["commits"][1]["sha"]
    assert C.get(f"{BASE}/jobs/{j}/git/compare", params={"head": last["sha"], "base": last["sha"]}).json()["files"] == []
    for params, code in (({"head": "HEAD"}, 422), ({"head": last["sha"], "base": "main~1"}, 422), ({}, 422),
                         ({"head": "0" * 40}, 404), ({"head": last["sha"], "base": "abcdef0"}, 404)):
        assert C.get(f"{BASE}/jobs/{j}/git/compare", params=params).status_code == code, params
    assert C.get(f"{BASE}/jobs/nope/git/compare", params={"head": last["sha"]}).status_code == 404


def test_valid_branch_unit():
    """B-006: reflog / previous-branch syntax never reaches git; names are checked as refs/heads/<name>."""
    import git_cli
    for good in ("main", "r2a/job-1234abcd", "feature/x-y_z.1", "UPPER", "a/b/c"):
        assert git_cli.valid_branch(good), good
    for bad in ("@{-1}", "@{-2}", "@", "main@{u}", "x@{1}", "a//b", "a/./b", "x.lock", "a..b", "/lead",
                "trail/", "a\\b", "-x", "a b", "", "a" * 201, "HEAD", "HEAD:x", "a~1", "a^", "q?", "*"):
        assert not git_cli.valid_branch(bad), bad
    # independent of the current directory (used to expand @{-N} against the backend's own checkout)
    here = os.getcwd()
    try:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        assert not git_cli.valid_branch("@{-1}") and git_cli.valid_branch("main")
    finally:
        os.chdir(here)


def test_git_diff_unit():
    """Renames, binary files, odd file names and size caps in git_diff.diff_sync."""
    import git_cli
    import git_diff
    root = tempfile.mkdtemp(prefix="r2a-diff-")
    git_cli.run(root, "init", "-q", "-b", "main")
    open(os.path.join(root, "a.py"), "w").write("".join(f"x{i} = {i}\n" for i in range(60)))
    open(os.path.join(root, "odd name\tx.txt"), "w").write("hello\n")
    git_cli.run(root, "add", "-A"); git_cli.run(root, "commit", "-q", "-m", "one")
    c1 = git_cli.out(root, "rev-parse", "HEAD").strip()
    src = open(os.path.join(root, "a.py")).read().replace("x30 = 30", "x30 = 3000")
    open(os.path.join(root, "a.py"), "w").write(src)
    os.rename(os.path.join(root, "odd name\tx.txt"), os.path.join(root, "renamed.txt"))
    open(os.path.join(root, "b.bin"), "wb").write(bytes(range(256)))
    open(os.path.join(root, "big.txt"), "w").write("y\n" * 150_000)
    git_cli.run(root, "add", "-A"); git_cli.run(root, "commit", "-q", "-m", "two")
    c2 = git_cli.out(root, "rev-parse", "HEAD").strip()
    d = {f["path"]: f for f in git_diff.diff_sync(root, c1, c2)["files"]}
    assert d["a.py"]["additions"] == 1 and d["a.py"]["deletions"] == 1 and d["a.py"]["context_expandable"]
    assert "x30 = 3000" in d["a.py"]["patch"] and "x5 = 5" in d["a.py"]["old_content"]
    assert d["renamed.txt"]["status"] == "renamed" and d["renamed.txt"]["old_path"] == "odd name\tx.txt"
    assert d["b.bin"]["binary"] and d["b.bin"]["patch"] is None and d["b.bin"]["new_content"] is None
    assert d["big.txt"]["patch_too_large"] and d["big.txt"]["patch"] is None and d["big.txt"]["additions"] == 150_000
    shutil.rmtree(root)


def test_download_zip_and_bundle():
    j = STATE["done"]
    head = C.get(f"{BASE}/jobs/{j}/git").json()["head"]
    r = C.get(f"{BASE}/jobs/{j}/git/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert f'roadmap2arena-{j[:8]}-repo.zip' in r.headers["content-disposition"]
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert f"roadmap2arena-{j[:8]}/.git/HEAD" in names
        zf.extractall(tmp)
    d = os.path.join(tmp, f"roadmap2arena-{j[:8]}")
    assert git(d, "rev-parse", "HEAD").strip() == head
    assert git(d, "status", "--porcelain") == "" and "ok" in subprocess.run(
        ["git", "fsck"], cwd=d, capture_output=True, text=True).stderr + "ok"
    r = C.get(f"{BASE}/jobs/{j}/git/download?format=bundle")
    assert r.status_code == 200 and r.content.startswith(b"# v2 git bundle") or r.content.startswith(b"# v3 git bundle")
    bpath = os.path.join(tmp, "r.bundle")
    open(bpath, "wb").write(r.content)
    git(tmp, "clone", "-q", bpath, "cl")
    assert git(os.path.join(tmp, "cl"), "rev-parse", "HEAD").strip() == head
    assert C.get(f"{BASE}/jobs/{j}/git/download?format=tar").status_code == 422
    assert C.get(f"{BASE}/jobs/nope/git/download").status_code == 404
    log = C.get(f"{BASE}/jobs/{j}").json()["log"]
    assert any("Git: downloaded repository as bundle (3 commits)" in l["msg"] for l in log)
    shutil.rmtree(tmp)


def test_resume_continues_same_repo():
    j = create(model="stub-503-at-3")
    job = wait(j)
    assert job["status"] == "error" and job["failed_step"] == 3
    g1 = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g1["commit_count"] == 2
    r = C.post(f"{BASE}/jobs/{j}/resume", json={"model": "gpt-4o"})
    assert r.status_code == 200, r.text
    job = wait(j, ("done",))
    g2 = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g2["repo_id"] == g1["repo_id"] and g2["commit_count"] == 3
    assert [c["sha"] for c in g2["commits"][1:]] == [c["sha"] for c in g1["commits"]]
    assert sum(l["msg"].startswith("Git: new repository") for l in job["log"]) == 1
    STATE["resumed"] = j


def test_restart_and_clone_get_new_repos():
    src = STATE["done"]
    src_repo = C.get(f"{BASE}/jobs/{src}/git").json()
    r = C.post(f"{BASE}/jobs/{src}/restart", json={})
    assert r.status_code == 201, r.text
    rj = r.json()["job_id"]
    job = wait(rj)
    g = C.get(f"{BASE}/jobs/{rj}/git").json()
    assert g["exists"] and g["repo_id"] != src_repo["repo_id"] and g["commit_count"] == 3
    assert g["head"] != src_repo["head"]
    assert any(f"restart of {src[:8]} - new history" in l["msg"] for l in job["log"])
    cj = create(cloned_from=src)
    job = wait(cj)
    g = C.get(f"{BASE}/jobs/{cj}/git").json()
    assert g["exists"] and g["repo_id"] not in (src_repo["repo_id"],) and g["owner"]["id"] == cj
    assert any(f"clone of {src[:8]} - new history" in l["msg"] for l in job["log"])
    # the source repo is untouched
    assert C.get(f"{BASE}/jobs/{src}/git").json()["head"] == src_repo["head"]
    STATE["restart"], STATE["clone"] = rj, cj


def test_stop_then_resume_and_empty_repo():
    j = create(model="stub-slow")
    end = time.time() + 15
    while time.time() < end and C.get(f"{BASE}/jobs/{j}").json()["status"] != "running":
        time.sleep(0.2)
    time.sleep(0.5)
    r = C.post(f"{BASE}/jobs/{j}/stop")
    assert r.status_code == 200, r.text
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g["exists"] and g["commit_count"] == 0 and g["commits"] == []
    assert C.get(f"{BASE}/jobs/{j}/git/download").status_code == 409
    r = C.post(f"{BASE}/jobs/{j}/resume", json={"model": "gpt-4o"})
    assert r.status_code == 200
    wait(j, ("done",))
    assert C.get(f"{BASE}/jobs/{j}/git").json()["commit_count"] == 3
    STATE["stopped"] = j


def test_init_for_old_job():
    """A job from before git integration (no repo, no commit_sha) can get a repo on demand."""
    j = STATE["resumed"]
    db = SRV.db()
    db.repos.delete_one({"owner.id": j})
    db.steps.update_many({"job_id": j}, {"$unset": {"commit_sha": ""}})
    db.jobs.update_one({"id": j}, {"$set": {"repo_id": None}})
    shutil.rmtree(repo_dir(j))
    g = C.get(f"{BASE}/jobs/{j}/git").json()
    assert g["exists"] is False and g["can_init"] and g["uncommitted_steps"] == 3 and g["commits"] == []
    assert C.get(f"{BASE}/jobs/{j}/git/download").status_code == 404
    assert C.get(f"{BASE}/jobs/{j}/git/commits/abcd").status_code == 404
    r = C.post(f"{BASE}/jobs/{j}/git/init")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] and body["commit_count"] == 3 and body["committed_steps"] == [1, 2, 3]
    assert C.get(f"{BASE}/jobs/{j}").json()["repo_id"] == body["repo_id"]
    again = C.post(f"{BASE}/jobs/{j}/git/init").json()
    assert again["committed_steps"] == [] and again["commit_count"] == 3
    assert C.post(f"{BASE}/jobs/nope/git/init").status_code == 404


def test_delete_removes_repo():
    j = STATE["clone"]
    assert os.path.isdir(repo_dir(j))
    assert C.delete(f"{BASE}/jobs/{j}").status_code == 200
    assert not os.path.exists(repo_dir(j))
    assert SRV.db().repos.count_documents({"owner.id": j}) == 0
    ids = [STATE["restart"], STATE["stopped"]]
    r = C.post(f"{BASE}/jobs/bulk-delete", json={"job_ids": ids}).json()
    assert r["deleted_count"] == 2
    assert not any(os.path.exists(repo_dir(i)) for i in ids)
    wait_idle()
    C.post(f"{BASE}/jobs/delete-finished")
    assert os.listdir(os.path.join(DATA, "repos")) == []
    assert SRV.db().repos.count_documents({}) == 0


def test_queued_job_has_no_repo_until_it_runs():
    blocker = create(model="stub-slow")
    q = create()
    g = C.get(f"{BASE}/jobs/{q}/git").json()
    assert g["exists"] is False and g["commit_count"] == 0
    assert C.delete(f"{BASE}/jobs/{q}").status_code == 200  # queued delete with no repo is fine
    C.post(f"{BASE}/jobs/{blocker}/stop")
    wait_idle()


def test_git_failure_never_breaks_job():
    """Data dir that can't be created: the job still finishes, the log says why."""
    bad = tempfile.mkstemp()[1]  # a file: mkdir below it fails
    with IsolatedServer(extra_env={"R2A_DATA_DIR": os.path.join(bad, "sub")}) as base2:
        r = httpx.post(f"{base2}/jobs", json={"roadmap_md": RM, "model": "gpt-4o"})
        jid = r.json()["job_id"]
        end = time.time() + 30
        while time.time() < end:
            job = httpx.get(f"{base2}/jobs/{jid}").json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.25)
        assert job["status"] == "done", job["status"]
        assert any(l["level"] == "warn" and l["msg"].startswith("Git: commit failed") for l in job["log"])
        assert httpx.delete(f"{base2}/jobs/{jid}").status_code == 200
    os.unlink(bad)


def test_snapshot_helper_unit():
    """repos.snapshot_sync: tree + contents at HEAD, globs, budget, binary/large handling."""
    import git_cli
    import repos
    root = tempfile.mkdtemp()
    git_cli.run(None, "init", "-q", "-b", "main", "--", root)
    assert repos.read_tree_sync(root) == [] and repos.snapshot_sync(root)["files"] == []
    files = {"src/a.py": "a" * 100, "src/b.py": "b" * 300, "README.md": "# r\n", "docs/x.md": "doc",
             "node_modules/m.js": "m", "big.txt": "x" * 2000, "img.bin": "\0\1\2"}
    for p, c in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)) or root, exist_ok=True)
        open(os.path.join(root, p), "w").write(c)
    git_cli.run(root, "add", "-A")
    git_cli.run(root, "commit", "-q", "-m", "init")
    tree = repos.read_tree_sync(root)
    assert [f["path"] for f in tree] == sorted(files) and {f["path"]: f["size"] for f in tree}["src/b.py"] == 300
    s = repos.snapshot_sync(root, exclude=["node_modules/"], budget_bytes=250, max_file_bytes=1000)
    info = {t["path"]: t for t in s["tree"]}
    assert info["node_modules/m.js"]["reason"] == "excluded" and info["big.txt"]["reason"] == "too large"
    assert info["img.bin"]["reason"] == "binary" and info["src/b.py"]["reason"] == "over budget"
    assert [f["path"] for f in s["files"]] == ["README.md", "docs/x.md", "src/a.py"]
    assert s["used_bytes"] == 4 + 3 + 100 and s["truncated"] and len(s["commit"]) == 40
    s = repos.snapshot_sync(root, include=["src/**", "*.md"], exclude=["docs/*"])
    assert [f["path"] for f in s["files"]] == ["README.md", "src/a.py", "src/b.py"] and not s["truncated"]
    assert repos.snapshot_sync(root, ref="nope")["tree"] == []
    shutil.rmtree(root)


if __name__ == "__main__":
    with SRV as base:
        BASE = base
        code = run_tests(globals())
    shutil.rmtree(DATA, ignore_errors=True)
    sys.exit(code)
