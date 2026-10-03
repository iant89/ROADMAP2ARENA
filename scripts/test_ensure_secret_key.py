#!/usr/bin/env python3
"""Tests for scripts/ensure_secret_key.py (temp dirs only, never touches backend/.env).

    ./venv/bin/python scripts/test_ensure_secret_key.py
"""
from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback

from cryptography.fernet import Fernet
from dotenv import dotenv_values

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ensure_secret_key.py")
NAME = "R2A_SECRET_KEY"
EXISTING = Fernet.generate_key().decode()
TMP = tempfile.mkdtemp(prefix="r2a_secret_test_")


def run(path: str) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, SCRIPT, path], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p


def case(name: str, content: str | None) -> str:
    d = os.path.join(TMP, name, "backend")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, ".env")
    if content is not None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.chmod(path, 0o644)
    return path


def key_of(path: str) -> str:
    return (dotenv_values(path, interpolate=False).get(NAME) or "").strip()


def mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def no_secret_in_output(p: subprocess.CompletedProcess, path: str) -> None:
    k = key_of(path)
    assert k and k not in p.stdout and k not in p.stderr, "key was printed"


def assert_generated(path: str, p: subprocess.CompletedProcess) -> str:
    k = key_of(path)
    Fernet(k.encode())  # valid Fernet key
    assert mode(path) == 0o600, oct(mode(path))
    assert "generated" in p.stdout, p.stdout
    no_secret_in_output(p, path)
    return k


OTHER = "MONGO_URL=mongodb://localhost:27017\nDB_NAME=roadmap2arena\n# a comment\nCORS_ORIGINS=*\n"


def test_no_env_file():
    path = case("missing", None)
    p = run(path)
    assert_generated(path, p)
    assert open(path).read().count(f"{NAME}=") == 1


def test_missing_dir_is_created():
    path = os.path.join(TMP, "nodir", "sub", ".env")
    p = run(path)
    assert_generated(path, p)


def test_env_without_key_keeps_other_lines():
    path = case("nokey", OTHER)
    p = run(path)
    assert_generated(path, p)
    text = open(path).read()
    assert text.startswith(OTHER), "other lines changed"
    assert text.count(f"{NAME}=") == 1


def test_env_without_trailing_newline():
    path = case("nonl", "DB_NAME=x")
    p = run(path)
    assert_generated(path, p)
    assert open(path).read().startswith("DB_NAME=x\n")
    assert dotenv_values(path)["DB_NAME"] == "x"


def test_env_with_empty_key_filled_in_place():
    content = "MONGO_URL=m\nR2A_SECRET_KEY=\nDB_NAME=d\n"
    path = case("empty", content)
    p = run(path)
    assert_generated(path, p)
    lines = open(path).read().splitlines()
    assert lines[0] == "MONGO_URL=m" and lines[2] == "DB_NAME=d" and lines[1].startswith(f"{NAME}=")
    assert len(lines) == 3


def test_empty_variants():
    for i, line in enumerate(['R2A_SECRET_KEY=""', "R2A_SECRET_KEY=''", "R2A_SECRET_KEY = ",
                              "export R2A_SECRET_KEY=", "R2A_SECRET_KEY= # set by setup.sh"]):
        path = case(f"variant{i}", f"A=1\n{line}\nB=2\n")
        p = run(path)
        assert_generated(path, p)
        d = dotenv_values(path)
        assert d["A"] == "1" and d["B"] == "2"
        assert open(path).read().count(NAME) == 1, line


def test_duplicate_empty_keys_collapsed():
    path = case("dupempty", "R2A_SECRET_KEY=\nX=1\nR2A_SECRET_KEY=\n")
    p = run(path)
    assert_generated(path, p)
    assert open(path).read().count(NAME) == 1


def test_existing_key_byte_identical():
    content = OTHER + f"R2A_SECRET_KEY={EXISTING}\nR2A_GITHUB_TOKEN=\n"
    path = case("existing", content)
    before = open(path, "rb").read()
    for _ in range(3):  # idempotent
        p = run(path)
        assert "kept" in p.stdout and EXISTING not in p.stdout + p.stderr
    assert open(path, "rb").read() == before
    assert mode(path) == 0o600


def test_existing_quoted_and_crlf_key_untouched():
    content = f'A=1\r\nexport R2A_SECRET_KEY="{EXISTING}"\r\n'
    path = case("quoted", content)
    before = open(path, "rb").read()
    p = run(path)
    assert "kept" in p.stdout
    assert open(path, "rb").read() == before


def test_invalid_existing_key_never_overwritten():
    path = case("invalid", "R2A_SECRET_KEY=not-a-fernet-key\n")
    before = open(path, "rb").read()
    p = run(path)
    assert open(path, "rb").read() == before
    assert "WARNING" in p.stderr and "not-a-fernet-key" not in p.stdout + p.stderr


def test_shadowed_key_is_kept_not_replaced():
    # dotenv: last assignment wins, so the trailing empty line hides the real key.
    path = case("shadow", f"R2A_SECRET_KEY={EXISTING}\nA=1\nR2A_SECRET_KEY=\n")
    p = run(path)
    assert key_of(path) == EXISTING, "existing key was replaced"
    assert open(path).read() == f"R2A_SECRET_KEY={EXISTING}\nA=1\n"
    assert EXISTING not in p.stdout + p.stderr


if __name__ == "__main__":
    names = [n for n in list(globals()) if n.startswith("test_")]
    failed = []
    for n in names:
        try:
            globals()[n]()
            print(f"PASS {n}")
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            print(f"FAIL {n}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(names) - len(failed)} passed, {len(failed)} failed: {failed}")
    sys.exit(1 if failed else 0)
