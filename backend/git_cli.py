"""Thin, safe wrapper around the git CLI (subprocess with argument lists, never a shell).

Every call runs with an isolated configuration so the host's git setup cannot leak in:
no system/global config, hooks disabled, no credential prompts, no GPG signing, a fixed
author/committer ("ROADMAP2ARENA"). Paths and refs that come from users are validated by
the callers before they get here; arguments are always passed as separate list items and
user values are placed after "--" where git allows it.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile

AUTHOR_NAME = "ROADMAP2ARENA"
AUTHOR_EMAIL = "roadmap2arena@localhost"
DEFAULT_BRANCH = "main"
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")
_BASE_CONFIG = ("-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                "-c", "tag.gpgsign=false", "-c", "core.quotepath=false", "-c", "protocol.ext.allow=never",
                "-c", "init.defaultBranch=" + DEFAULT_BRANCH, "-c", "advice.detachedHead=false")


class GitError(Exception):
    def __init__(self, message: str, returncode: int = 1, stderr: str = ""):
        super().__init__(message)
        self.returncode, self.stderr = returncode, stderr


def git_env(extra: dict | None = None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("R2A_GIT_HOME", "/nonexistent"),
        "LANG": "C", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
        "GIT_AUTHOR_NAME": AUTHOR_NAME, "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME, "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
    }
    if extra:
        env.update(extra)
    return env


def run(cwd: str | None, *args: str, env: dict | None = None, timeout: float = 60,
        check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Run `git <args>` synchronously. Raises GitError on a non-zero exit when check=True."""
    cmd = ["git", *_BASE_CONFIG, *args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=git_env(env), capture_output=True, timeout=timeout,
                              input=input_bytes, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout:g}s") from exc
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {args[0]} failed: {err[-500:] or f'exit {proc.returncode}'}", proc.returncode, err)
    return proc


def out(cwd: str | None, *args: str, **kw) -> str:
    return run(cwd, *args, **kw).stdout.decode("utf-8", "replace")


async def arun(cwd: str | None, *args: str, **kw) -> subprocess.CompletedProcess:
    return await asyncio.to_thread(run, cwd, *args, **kw)


async def aout(cwd: str | None, *args: str, **kw) -> str:
    return await asyncio.to_thread(out, cwd, *args, **kw)


def valid_branch(name: str) -> bool:
    """True if `name` is a valid, unambiguous branch name.

    Reflog/previous-branch syntax ("@{-1}", "main@{u}", a bare "@") is rejected outright: with
    `check-ref-format --branch` git expands it against the repository in the current directory
    (the backend's own checkout), so it must never reach git. The name is checked as
    refs/heads/<name> with --normalize in an empty temp dir and must already be normalised.
    """
    if (not name or len(name) > 200 or name.startswith("-") or any(c.isspace() for c in name)
            or "@{" in name or name in ("@", "HEAD")):
        return False
    ref = f"refs/heads/{name}"
    with tempfile.TemporaryDirectory(prefix="r2a-refcheck-") as tmp:
        proc = run(tmp, "check-ref-format", "--normalize", ref, check=False)
    return proc.returncode == 0 and proc.stdout.decode("utf-8", "replace").strip() == ref
