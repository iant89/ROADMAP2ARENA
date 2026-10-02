"""F-006: job repos must never trigger uvicorn --reload (it killed running jobs).

1. Static: the default data dir is outside backend/; the reload flags in deploy/supervisor/backend.conf
   and run.sh exclude tests/, data/ and git files (checked with uvicorn's own Config/FileFilter).
2. Live: start the worktree backend with exactly the supervisor reload flags (paths re-pointed at
   this checkout, throwaway DB, DEFAULT data dir), then
   - write .py files into a job repo dir, deep under tests/ and a git dir -> no reload;
   - run a real job whose step writes app/main.py into its repo -> it finishes "done", no reload;
   - positive control: a new backend/*.py file DOES reload (so the watcher was really active).

    TEST_STUB_URL=http://127.0.0.1:<arena stub> /app/venv/bin/python tests/test_reload_isolation.py
(without TEST_STUB_URL the real-job part is skipped)
"""
from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.dirname(BACKEND)
sys.path.insert(0, HERE)
sys.path.insert(0, BACKEND)
from isolated_server import free_port, run_tests, _env_file  # noqa: E402

CONF = os.path.join(ROOT, "deploy", "supervisor", "backend.conf")
STUB = os.environ.get("TEST_STUB_URL")


def conf_args() -> list[str]:
    """uvicorn argv from deploy/supervisor/backend.conf with /app/backend re-pointed at this checkout."""
    line = next(l for l in open(CONF) if l.startswith("command="))[len("command="):]
    argv = shlex.split(line.replace("/app/backend", BACKEND))
    assert argv[0].endswith("/uvicorn") and argv[1] == "server:app"
    return argv[2:]


def opts(argv: list[str], name: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == name]


def test_default_data_dir_outside_backend():
    env = {k: v for k, v in os.environ.items() if k != "R2A_DATA_DIR"}
    out = subprocess.run([sys.executable, "-c", "import repos; print(repos.data_dir())"], cwd=BACKEND, env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == os.path.join(ROOT, "data"), out
    assert not (out + os.sep).startswith(BACKEND + os.sep)


def test_reload_flags_exclude_repos_tests_git():
    from uvicorn.config import Config
    from uvicorn.supervisors.watchfilesreload import FileFilter  # needs watchfiles (requirements.txt)
    argv = conf_args()
    assert "--reload" in argv and opts(argv, "--reload-dir") == [BACKEND]
    ex = opts(argv, "--reload-exclude")
    assert {"tests/*", "data/*", "*.git*", os.path.join(BACKEND, "tests")} <= set(ex), ex
    here = os.getcwd()
    os.chdir(BACKEND)
    try:
        cfg = Config("server:app", reload=True, reload_dirs=opts(argv, "--reload-dir"), reload_excludes=ex)
        flt = FileFilter(cfg)
    finally:
        os.chdir(here)
    dirs = [str(d) for d in cfg.reload_dirs]
    assert dirs == [BACKEND], dirs
    data = os.path.join(ROOT, "data")
    assert not any((data + os.sep).startswith(d + os.sep) for d in dirs), "data dir is inside a reload dir"
    b = Path(BACKEND)
    # (uvicorn matches glob excludes against the tail of the path: "*.git*" covers file names)
    for p in ("tests/x.py", "tests/a/b/c.py", "data/x.py", "hook.git.py"):
        assert not flt(b / p), p
    for p in ("server.py", "git_cli.py", "sub/mod.py"):
        assert flt(b / p), p
    # run.sh uses the same excludes
    run = open(os.path.join(ROOT, "run.sh")).read()
    for pat in ("--reload-dir \"$PWD\"", "--reload-exclude \"$PWD/tests\"", "'tests/*'", "'data/*'", "'*.git*'"):
        assert pat in run, pat


def test_migrate_legacy_data():
    import tempfile
    import repos
    tmp = tempfile.mkdtemp(prefix="r2a-migrate-")
    old, new = os.path.join(tmp, "backend-data"), os.path.join(tmp, "data")
    for name in ("job-a", "job-b"):
        os.makedirs(os.path.join(old, "repos", name, ".git"))
        open(os.path.join(old, "repos", name, "f.txt"), "w").write(name)
    os.makedirs(os.path.join(new, "repos", "job-b"))  # already present at the new path -> never overwritten
    saved = {k: os.environ.get(k) for k in ("R2A_DATA_DIR", "R2A_MIGRATE_LEGACY_DATA")}
    try:
        os.environ["R2A_DATA_DIR"] = new
        os.environ["R2A_MIGRATE_LEGACY_DATA"] = "0"
        assert repos.migrate_legacy_data(old) == {"moved": [], "skipped": []}  # opt-out (test servers)
        os.environ.pop("R2A_MIGRATE_LEGACY_DATA")
        res = repos.migrate_legacy_data(old)
        assert res == {"moved": ["repos/job-a"], "skipped": ["repos/job-b"]}, res
        assert open(os.path.join(new, "repos", "job-a", "f.txt")).read() == "job-a"
        assert os.path.isdir(os.path.join(new, "repos", "job-a", ".git"))
        assert os.path.exists(os.path.join(old, "repos", "job-b", "f.txt"))  # left in place
        assert not os.path.exists(os.path.join(new, "repos", "job-b", "f.txt"))
        assert repos.migrate_legacy_data(old) == {"moved": [], "skipped": ["repos/job-b"]}  # idempotent
        shutil.rmtree(os.path.join(old, "repos", "job-b"))
        repos.migrate_legacy_data(old)
        assert not os.path.exists(old)  # empty legacy dirs removed
        assert repos.migrate_legacy_data(os.path.join(tmp, "nope")) == {"moved": [], "skipped": []}
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(tmp, ignore_errors=True)


class ReloadingServer:
    def __init__(self):
        self.port = free_port()
        self.db_name = f"roadmap2arena_test_{secrets.token_hex(4)}"
        self.base = f"http://127.0.0.1:{self.port}/api"
        self.log_path = f"/tmp/{self.db_name}.reload.log"

    def __enter__(self):
        env = {k: v for k, v in os.environ.items() if k != "R2A_DATA_DIR"}  # default data dir on purpose
        env.update({"DB_NAME": self.db_name, "ARENA_STEP_DELAY_SECONDS": "0.2", "PYTHONDONTWRITEBYTECODE": "1",
                    "R2A_MIGRATE_LEGACY_DATA": "0", "PYTHONUNBUFFERED": "1"})
        if STUB:
            env["ARENA2API_URL"] = STUB
        self._log = open(self.log_path, "w")
        uv = os.path.join(os.path.dirname(sys.executable), "uvicorn")
        argv = [a if a != "8001" else str(self.port) for a in conf_args()]
        self.proc = subprocess.Popen([uv, "server:app", *argv], cwd=BACKEND, env=env, stdout=self._log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        end = time.time() + 40
        while time.time() < end:
            try:
                if httpx.get(f"{self.base}/queue", timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.3)
        raise RuntimeError("reloading backend did not start: " + self.log()[-1500:])

    def log(self) -> str:
        return open(self.log_path).read()

    def reloads(self) -> int:
        return len(re.findall(r"WatchFiles detected changes|StatReload detected", self.log()))

    def __exit__(self, *exc):
        import signal
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(15)
        except Exception:  # noqa: BLE001
            os.killpg(self.proc.pid, signal.SIGKILL)
        self._log.close()
        try:
            from pymongo import MongoClient
            MongoClient(_env_file()["MONGO_URL"], serverSelectionTimeoutMS=5000).drop_database(self.db_name)
        except Exception:  # noqa: BLE001
            pass


def test_live_no_reload_from_job_repo_or_tests():
    tag = secrets.token_hex(3)
    data = os.path.join(ROOT, "data")
    created = [os.path.join(data, "repos", f"zz-reload-probe-{tag}"), os.path.join(HERE, f"_reload_probe_{tag}")]
    probe_src = os.path.join(BACKEND, f"_reload_probe_{tag}.py")
    with ReloadingServer() as srv:
        assert "using WatchFiles" in srv.log(), srv.log()[-800:]
        assert f"Will watch for changes in these directories: ['{BACKEND}']" in srv.log()
        time.sleep(1.5)
        try:
            for f in (f"{created[0]}/app/main.py", f"{created[0]}/.git/hooks/x.py",
                      f"{created[1]}/app/main.py", f"{created[1]}/deep/er/x.py", f"{HERE}/_reload_probe_{tag}.py"):
                os.makedirs(os.path.dirname(f), exist_ok=True)
                open(f, "w").write("print('hello')\n")
            created.append(f"{HERE}/_reload_probe_{tag}.py")
            time.sleep(4)
            assert srv.reloads() == 0, srv.log()[-1500:]

            if STUB:  # the tester's F-006 scenario: a step writes app/main.py into the job's repo
                r = httpx.post(f"{srv.base}/jobs", json={"roadmap_md": "# Reload\n\n### One\nx\n\n### Two\ny\n\n### Three\nz\n",
                                                          "model": "gpt-4o"}, timeout=20)
                assert r.status_code == 201, r.text
                j = r.json()["job_id"]
                end = time.time() + 120
                while time.time() < end:
                    job = httpx.get(f"{srv.base}/jobs/{j}", timeout=10).json()
                    if job["status"] not in ("queued", "running"):
                        break
                    time.sleep(0.5)
                git = httpx.get(f"{srv.base}/jobs/{j}/git", timeout=10).json()
                assert job["status"] == "done", (job["status"], [l["msg"] for l in job["log"]][-5:])
                assert not any("interrupted" in l["msg"].lower() for l in job["log"])
                repo_dir = os.path.join(data, git["path"])  # path is relative to the data dir
                assert git["path"] == f"repos/{j}" and os.path.exists(os.path.join(repo_dir, "app", "main.py")), git["path"]
                time.sleep(2.5)
                assert srv.reloads() == 0, srv.log()[-1500:]
                assert httpx.delete(f"{srv.base}/jobs/{j}", timeout=20).status_code in (200, 204)
                assert not os.path.exists(repo_dir)
            else:
                print("  (TEST_STUB_URL not set - real-job part skipped)")

            # positive control: backend source changes still reload
            open(probe_src, "w").write("X = 1\n")
            end = time.time() + 15
            while time.time() < end and srv.reloads() == 0:
                time.sleep(0.3)
            assert srv.reloads() >= 1, "backend source change did not reload - watcher inactive?"
        finally:
            for p in created:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.exists(p):
                    os.remove(p)
            if os.path.exists(probe_src):
                os.remove(probe_src)


if __name__ == "__main__":
    sys.exit(run_tests(globals()))
