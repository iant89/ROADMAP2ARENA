"""Environment-driven settings. All values come from backend/.env (no code defaults)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (see backend/.env.example)")
    return value


MONGO_URL = _required("MONGO_URL")
DB_NAME = _required("DB_NAME")
ARENA2API_URL = _required("ARENA2API_URL")
ARENA2API_MODEL = _required("ARENA2API_MODEL")
ARENA_STEP_DELAY_SECONDS = float(_required("ARENA_STEP_DELAY_SECONDS"))
ARENA_REQUEST_TIMEOUT_SECONDS = float(_required("ARENA_REQUEST_TIMEOUT_SECONDS"))
CORS_ORIGINS = [o.strip() for o in _required("CORS_ORIGINS").split(",") if o.strip()]
