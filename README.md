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
