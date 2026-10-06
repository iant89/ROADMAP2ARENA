"""Project import/workspace lifecycle against local GitHub and Arena stand-ins.

The real backend, MongoDB, local GitHub REST fixture, and normal Arena stub are used;
there are no external GitHub/provider requests. Included in test-integration.sh.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.fernet import Fernet

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
from isolated_server import IsolatedServer, free_port, run_tests  # noqa: E402

TOKEN = "ghp_validtoken000000000000000000000000"
STUB_URL = os.environ.get("TEST_STUB_URL", "http://127.0.0.1:9090")
TMP = tempfile.mkdtemp(prefix="r2a-project-test-")
GH_ROOT = os.path.join(TMP, "github")
PROJECT_DATA_DIR = os.path.join(TMP, "data")
GH_PORT = free_port()
GH = f"http://127.0.0.1:{GH_PORT}"
KEY = Fernet.generate_key().decode()
GH_ENV = {"GITHUB_API_URL": GH, "GITHUB_OAUTH_URL": GH, "GITHUB_OAUTH_CLIENT_ID": "",
          "R2A_GITHUB_TOKEN": "", "R2A_GITHUB_ALLOW_FILE_REMOTES": "1"}
SRV = IsolatedServer(step_delay=0, extra_env={"R2A_DATA_DIR": PROJECT_DATA_DIR,
                                               "R2A_SECRET_KEY": KEY, **GH_ENV})
BASE = ""
C = httpx.Client(timeout=60)
STATE: dict = {}
GIT_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Project Test", "GIT_AUTHOR_EMAIL": "project-test@example.invalid",
           "GIT_COMMITTER_NAME": "Project Test", "GIT_COMMITTER_EMAIL": "project-test@example.invalid"}
ROADMAP = "# Project update\n\n### First step\nAdd a health endpoint.\n\n### Second step\nAdd a model.\n\n### Third step\nAdd a route.\n"


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=check).stdout


def create_project(**body):
    response = C.post(f"{BASE}/projects", json={"repo_full_name": "r2a-tester/existing-repo", **body})
    assert response.status_code == 201, response.text
    return response.json()


def create_job(project_id, extra_context="job-specific notes"):
    response = C.post(f"{BASE}/jobs", json={
        "project_id": project_id, "project_context": extra_context, "roadmap_md": ROADMAP,
        "model": "gpt-4o", "arena_url": STUB_URL,
    })
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def wait(job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = C.get(f"{BASE}/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in ("done", "error", "stopped", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


def test_project_requires_github_and_validates_repo_names():
    assert C.get(f"{BASE}/projects").json() == []
    invalid = C.post(f"{BASE}/projects", json={"repo_full_name": "../outside"})
    assert invalid.status_code == 422, invalid.text
    not_connected = C.post(f"{BASE}/projects", json={"repo_full_name": "r2a-tester/existing-repo"})
    assert not_connected.status_code == 409, not_connected.text
    connected = C.put(f"{BASE}/github/token", json={"token": TOKEN})
    assert connected.status_code == 200, connected.text
    bad_branch = C.post(f"{BASE}/projects", json={"repo_full_name": "r2a-tester/existing-repo", "branch": "@{-1}"})
    assert bad_branch.status_code == 422, bad_branch.text


def test_import_persist_and_update_project():
    project = create_project(name="Tasky", context="Use FastAPI and keep routes typed.")
    assert project["name"] == "Tasky"
    assert project["source"]["repo_full_name"] == "r2a-tester/existing-repo"
    assert project["source"]["branch"] == "main" and project["source"]["private"] is False
    assert len(project["head"]) == 40 and project["commit_count"] == 1 and project["job_count"] == 0
    assert C.get(f"{BASE}/projects/{project['id']}").json() == project
    assert C.get(f"{BASE}/projects").json() == [project]

    repo_doc = SRV.db().repos.find_one({"owner.type": "project", "owner.id": project["id"]}, {"_id": 0})
    assert repo_doc and repo_doc["head"] == project["head"]
    root = os.path.join(PROJECT_DATA_DIR, repo_doc["path"])
    assert os.path.isfile(os.path.join(root, "README.md"))
    remote = git(root, "remote", "get-url", "origin").strip()
    assert TOKEN not in remote
    assert TOKEN not in open(os.path.join(root, ".git", "config")).read()

    updated = C.put(f"{BASE}/projects/{project['id']}", json={"name": "Tasky API", "context": "Use async FastAPI."})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Tasky API" and updated.json()["context"] == "Use async FastAPI."
    assert C.put(f"{BASE}/projects/{project['id']}", json={}).status_code == 422
    STATE.update(project=updated.json(), project_path=root)


def test_project_job_uses_private_pinned_repo_and_source_prompt():
    project = STATE["project"]
    job_id = create_job(project["id"])
    job = wait(job_id)
    assert job["status"] == "done", job.get("error")
    assert job["project_id"] == project["id"] and job["project_name"] == "Tasky API"
    assert job["project_commit"] == project["head"]
    stored_job = SRV.db().jobs.find_one({"id": job_id}, {"_id": 0})
    assert stored_job["project_base_commit"] == project["head"]  # local non-shallow fixture
    assert "Use async FastAPI." in job["project_context"] and "job-specific notes" in job["project_context"]
    first = C.get(f"{BASE}/jobs/{job_id}/steps/1").json()
    assert "INITIAL REPOSITORY SNAPSHOT" in first["prompt"]
    assert 'FILE "README.md"' in first["prompt"] and "existing project" in first["prompt"]
    assert "PROJECT INSTRUCTIONS" in first["prompt"] and "job-specific notes" in first["prompt"]

    git_info = C.get(f"{BASE}/jobs/{job_id}/git").json()
    assert git_info["owner"] == {"type": "job", "id": job_id}
    assert git_info["commit_count"] == 4  # imported base plus three generated step commits
    source = C.get(f"{BASE}/projects/{project['id']}").json()
    assert source["head"] == project["head"] and source["commit_count"] == 1
    assert git(STATE["project_path"], "status", "--porcelain") == ""
    STATE["first_job"] = job_id


def test_refresh_moves_new_jobs_but_restart_keeps_original_revision():
    project, first_job = STATE["project"], STATE["first_job"]
    remote = os.path.join(GH_ROOT, "r2a-tester", "existing-repo.git")
    work = os.path.join(TMP, "update-worktree")
    git(None, "clone", "-q", remote, work)
    with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as f:
        f.write("\nUpdated upstream source.\n")
    git(work, "add", "README.md")
    git(work, "commit", "-qm", "update source")
    new_source_commit = git(work, "rev-parse", "HEAD").strip()
    git(work, "push", "-q", "origin", "main")

    refreshed = C.post(f"{BASE}/projects/{project['id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["head"] == new_source_commit
    assert refreshed.json()["head"] != project["head"]

    new_job = create_job(project["id"], "after refresh")
    new_done = wait(new_job)
    assert new_done["status"] == "done", new_done.get("error")
    prompt = C.get(f"{BASE}/jobs/{new_job}/steps/1").json()["prompt"]
    assert "Updated upstream source." in prompt
    assert new_done["project_commit"] == new_source_commit

    restarted = C.post(f"{BASE}/jobs/{first_job}/restart", json={})
    assert restarted.status_code == 201, restarted.text
    restart_id = restarted.json()["job_id"]
    restarted_job = wait(restart_id)
    assert restarted_job["status"] == "done", restarted_job.get("error")
    assert restarted_job["project_commit"] == project["head"]
    old_prompt = C.get(f"{BASE}/jobs/{restart_id}/steps/1").json()["prompt"]
    assert "Updated upstream source." not in old_prompt
    STATE["other_jobs"] = [new_job, restart_id]


def test_project_deletion_preserves_linked_jobs_until_they_are_removed():
    project = STATE["project"]
    conflict = C.delete(f"{BASE}/projects/{project['id']}")
    assert conflict.status_code == 409, conflict.text
    for job_id in [STATE["first_job"], *STATE["other_jobs"]]:
        response = C.delete(f"{BASE}/jobs/{job_id}")
        assert response.status_code == 200, response.text
    deleted = C.delete(f"{BASE}/projects/{project['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True, "project_id": project["id"]}
    assert not os.path.exists(STATE["project_path"])
    assert C.get(f"{BASE}/projects/{project['id']}").status_code == 404
    assert C.get(f"{BASE}/projects").json() == []


if __name__ == "__main__":
    stub = subprocess.Popen([sys.executable, os.path.join(HERE, "github_stub.py"), "--port", str(GH_PORT),
                             "--root", GH_ROOT], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    code = 1
    try:
        for _ in range(100):
            try:
                httpx.get(f"{GH}/_stub/requests", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        with SRV as base:
            BASE = base
            code = run_tests(globals())
    finally:
        C.close()
        stub.terminate()
        try:
            stub.wait(10)
        except subprocess.TimeoutExpired:
            stub.kill()
            stub.wait(5)
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
