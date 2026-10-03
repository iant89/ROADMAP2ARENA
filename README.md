# ROADMAP2ARENA

Reads a ROADMAP.md and completes it step by step by sending each step to Arena.ai
through the OpenAI-compatible arena2api gateway, collecting the generated files and
offering them as a ZIP.

Current state: **mock frontend only** - every job is simulated in the browser
(`frontend/src/lib/mockEngine.js`, data in `frontend/src/mock.js`). All data access goes
through `frontend/src/lib/api.js`, which is the single place to swap in a real backend.

- `frontend/` - React + Vite + Tailwind + shadcn/ui
- `backend/` - placeholder (requirements only)
- `./run.sh` - local dev (Vite on 5173, backend on 8001 once `backend/server.py` exists)
- `./setup.sh` - Ubuntu server setup (Node, yarn, MongoDB, Caddy, systemd)

## Setup notes

- `./setup.sh` creates `backend/.env` from `backend/.env.example` (other values left as they are)
  and **generates `R2A_SECRET_KEY` automatically** if it is missing or empty. That key is the Fernet
  key that encrypts the stored GitHub token. Re-running setup never overwrites an existing key.
  The key is never printed, and the file is set to mode 600.
- To do only that step: `./venv/bin/python scripts/ensure_secret_key.py backend/.env`
  (tests: `./venv/bin/python scripts/test_ensure_secret_key.py`, temp dirs only).
- Back up `backend/.env`. If the key is lost or changed, the saved GitHub token can't be decrypted
  and you have to reconnect GitHub in Settings.
