"""Configuration and loading for the unmodified, pinned upstream gateway.

The gateway is a separate process, not mounted into ROADMAP2ARENA's /api app.
Its in-memory extension/session store must be served by exactly one worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import re
import subprocess
from types import ModuleType
from typing import Mapping

from dotenv import dotenv_values

from . import REPO_ROOT, UPSTREAM_DIR, UPSTREAM_REVISION

DEFAULT_ENV_FILE = REPO_ROOT / "gateway" / ".env"
LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 9090
    api_key: str = field(default="", repr=False)
    log_level: str = "info"

    def __post_init__(self):
        if not self.host or not re.fullmatch(r"[A-Za-z0-9_.:-]+", self.host):
            raise ValueError("GATEWAY_HOST must be a host/IP address, not a URL or whitespace")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("GATEWAY_PORT must be an integer from 1 to 65535")
        if self.log_level not in LOG_LEVELS:
            raise ValueError("GATEWAY_LOG_LEVEL must be critical, error, warning, info, debug or trace")
        if any(ord(c) < 32 or ord(c) > 126 for c in self.api_key):
            raise ValueError("GATEWAY_API_KEY must contain only printable ASCII characters")

    @classmethod
    def load(cls, env_file: Path | None = None, *, environ: Mapping[str, str] | None = None,
             host: str | None = None, port: int | None = None) -> "GatewayConfig":
        path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE
        if env_file is not None and not path.is_file():
            raise ValueError("The requested gateway environment file does not exist")
        defaults = {k: v for k, v in dotenv_values(path).items() if v is not None}
        values = {**defaults, **(os.environ if environ is None else environ)}
        try:
            selected_port = port if port is not None else int(values.get("GATEWAY_PORT", "9090"))
        except (TypeError, ValueError) as exc:
            raise ValueError("GATEWAY_PORT must be an integer from 1 to 65535") from exc
        return cls(
            host=host if host is not None else values.get("GATEWAY_HOST", "127.0.0.1").strip(),
            port=selected_port,
            api_key=values.get("GATEWAY_API_KEY", "").strip(),
            log_level=values.get("GATEWAY_LOG_LEVEL", "info").strip().lower(),
        )


def check_upstream() -> str:
    """Verify the local checkout, without fetching or following upstream main."""
    if not (UPSTREAM_DIR / "server.py").is_file():
        raise RuntimeError(
            "arena2api is not initialised. Run: git submodule update --init --recursive services/arena2api")
    proc = subprocess.run(["git", "-C", str(UPSTREAM_DIR), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=10, check=False)
    if proc.returncode != 0 or proc.stdout.strip() != UPSTREAM_REVISION:
        raise RuntimeError(
            "arena2api does not match the pinned revision. Run: git submodule update --init --recursive services/arena2api")
    return proc.stdout.strip()


def load_upstream(config: GatewayConfig) -> ModuleType:
    """Import server.py under its own module name, without starting a listener.

    Upstream reads API_KEY/PORT/DEBUG at import time. Apply only our gateway
    settings temporarily; do not let unrelated API_KEY/DEBUG variables configure it.
    No Arena requests or browser interactions occur during import.
    """
    source = UPSTREAM_DIR / "server.py"
    if not source.is_file():
        raise RuntimeError("arena2api is not initialised; run git submodule update --init --recursive services/arena2api")
    old = {k: os.environ.get(k) for k in ("API_KEY", "PORT", "DEBUG")}
    try:
        os.environ["API_KEY"] = config.api_key
        os.environ["PORT"] = str(config.port)
        if config.log_level in ("debug", "trace"):
            os.environ["DEBUG"] = "1"
        else:
            os.environ.pop("DEBUG", None)
        spec = importlib.util.spec_from_file_location("r2a_arena2api_upstream", source)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load the arena2api server")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
