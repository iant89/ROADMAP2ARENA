"""One-command Mongo-backed validation driver. No real providers or .env required.

TEST_MONGO_URL is explicit, loopback-only and never substitutes a mock database.
Run ./test-integration.sh to provision an ephemeral Docker MongoDB automatically.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from integration_support import LocalService, ROOT, require_mongo, safe_environment, test_mongo_url

# Every listed suite owns an IsolatedServer/throwaway DB. Legacy direct/destructive
# scripts are deliberately excluded until adapted to the same safety contract.
REGRESSION_SUITES = (
    "test_deletion.py", "test_notifications.py", "test_git.py", "test_github.py", "test_projects.py", "test_reload_isolation.py",
)
SELF_CONTAINED = ("test_core.py", "test_gateway.py", "test_github_client.py", "test_integration_support.py")


def reload_sandbox(source: Path, target: Path) -> Path:
    """The positive-control reload test must not edit the user's checkout/data."""
    shutil.copytree(source / "backend", target / "backend",
                    ignore=shutil.ignore_patterns(".env", ".env.*", "__pycache__", "data", "*.log", ".git"))
    (target / "deploy/supervisor").mkdir(parents=True)
    shutil.copy2(source / "deploy/supervisor/backend.conf", target / "deploy/supervisor/backend.conf")
    shutil.copy2(source / "run.sh", target / "run.sh")
    return target


def run_script(name: str, env: dict, timeout: int = 600, *, root: Path = ROOT):
    print(f"\n=== {name} ===", flush=True)
    result = subprocess.run([sys.executable, str(root / "backend/tests" / name)],
                            cwd=root, env=env, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} failed (exit {result.returncode}); validation stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-only", action="store_true", help="Run self-contained checks + new gateway pipeline; skip older DB suites")
    args = parser.parse_args(argv)
    try:
        uri = test_mongo_url()
        version = require_mongo(uri)
        print(f"MongoDB {version} available; only throwaway DBs/local providers will be used", flush=True)
        with tempfile.TemporaryDirectory(prefix="r2a-integration-") as data:
            env = safe_environment(uri)
            env["R2A_DATA_DIR"] = data
            for name in SELF_CONTAINED:
                run_script(name, env)
            run_script("test_gateway_pipeline.py", env)
            if not args.pipeline_only:
                command = [sys.executable, "-m", "uvicorn", "tests.arena_stub:app", "--host", "127.0.0.1", "--port", "{port}"]
                with LocalService(command, cwd=ROOT / "backend", env=env, ready_path="/v1/models") as stub:
                    env.update(TEST_STUB_URL=stub.base, ARENA2API_URL=stub.base)
                    # No real key; this is the canned local stand-in, not the gateway.
                    for name in REGRESSION_SUITES:
                        if name == "test_reload_isolation.py":
                            with tempfile.TemporaryDirectory(prefix="r2a-reload-copy-") as copy:
                                run_script(name, env, root=reload_sandbox(ROOT, Path(copy)))
                        else:
                            run_script(name, env)
        print("\nPASS: requested integration suites completed; no real Arena/GitHub/provider calls", flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Integration validation stopped: {error}", file=sys.stderr)
        print("Use ./test-integration.sh (Docker Compose) or supply an explicit loopback TEST_MONGO_URL.", file=sys.stderr)
        return 1
    except Exception as error:
        # Server-selection/connect errors are reported without a raw credential URI.
        print(f"Mongo/integration preflight failed ({type(error).__name__}). No mock database was substituted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
