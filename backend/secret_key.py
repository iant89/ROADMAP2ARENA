"""R2A_SECRET_KEY helpers (Fernet key from backend/.env) shared by encrypted settings.

The GitHub token (github_client.py) and provider API keys (providers.py) are encrypted with
the same key. Changing or losing the key makes stored secrets unreadable.
"""
from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger("roadmap2arena.secret_key")

KEY_HINT = ("Set a valid R2A_SECRET_KEY in backend/.env (generate one with: ./venv/bin/python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\") and restart the backend")


def fernet() -> Fernet | None:
    key = os.environ.get("R2A_SECRET_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except ValueError:
        logger.warning("R2A_SECRET_KEY in backend/.env is not a valid Fernet key")
        return None


def key_state() -> str:
    """ok | missing | invalid"""
    if not os.environ.get("R2A_SECRET_KEY", "").strip():
        return "missing"
    return "ok" if fernet() else "invalid"
