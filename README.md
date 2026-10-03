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

## Providers

Jobs can run against any OpenAI-compatible API. In **Settings > Providers** add a named
provider (presets: OpenAI, OpenRouter, Groq, Ollama, LM Studio, arena2api (local), custom)
with its base URL, an optional API key and optional extra headers; use **Test connection**
/ **Fetch models** to check it (`GET {base}/models`), and mark one as the default. The
Create job form then picks a provider and a model (fetched list or free text).

- API keys are encrypted with `R2A_SECRET_KEY` (Fernet, same as the GitHub token), are
  write-only and are never returned, logged, copied to jobs or included in error messages.
  A stored key is only sent to the host it was saved for.
- Jobs store the provider id plus a non-secret snapshot (name, base URL).
- On first start after upgrading, the existing arena2api URL setting becomes the default
  provider "arena2api (local)", so existing setups and old jobs keep working.
