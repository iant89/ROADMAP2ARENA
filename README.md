# ROADMAP2ARENA

Turn a Markdown roadmap into generated files, one step at a time. ROADMAP2ARENA
sends each step to an OpenAI-compatible **arena2api** gateway in a continuous
conversation, saves the results in MongoDB, and lets you review or export them.

**Current state:** a React dashboard with a FastAPI + MongoDB job backend and an
opt-in, pinned [flay-o/arena2api](https://github.com/flay-o/arena2api) gateway.
The gateway is a **separate Python service**, not a replacement for the job
backend. Real Arena access still requires manually connecting its browser
extension. See the [gateway setup and security guide](docs/arena2api.md).

## Features

- Parse `### Step title` headings or standalone `- [ ] Task` items into ordered steps.
- Queue jobs with reorder, pause/unpause, and cancel; only one job runs at a time.
- Stop a running job, resume an interrupted/failed job, or restart it as a new job.
- Browse job history, transcripts, logs, step details, and generated file versions.
- Download individual files, a latest-files ZIP, or a standalone HTML transcript.
- Clone job inputs and permanently delete jobs individually or in bulk.
- Record a local Git commit for every completed step, review/compare diffs, and
  download the repository (including `.git`) or a Git bundle.
- Connect GitHub using a personal access token or OAuth device flow; push to a new
  or existing repository, optionally open a PR, or enable auto-push on completion.
- Receive in-app alerts, browser notifications, webhooks, and SMTP email, with
  per-event settings; watch pushed GitHub branches for PR and CI changes.
- Save runtime defaults for the gateway URL, model, step delay, and request timeout.
- Run jobs against any OpenAI-compatible provider (OpenAI, OpenRouter, Groq, Ollama,
  LM Studio, arena2api or a custom endpoint) - see [Providers](#providers).
- Run the included real gateway independently or alongside the app, with an
  optional server-only HTTP API key.

Jobs currently have independent repositories. **Projects / importing an existing
repository as job context is planned, not implemented.** Generated code is stored
and committed, not executed or automatically validated by this app.

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
- On first start after upgrading, the existing gateway URL setting becomes the default
  provider "arena2api (local)" (`<url>/v1`), so existing setups and old jobs keep working.
- `ARENA2API_API_KEY` stays server-side: a provider **without its own key** whose base URL
  is exactly `ARENA2API_URL` + `/v1` (such as the migrated provider) uses it, and legacy
  jobs keep the exact-`ARENA2API_URL` rule. A key saved on the provider takes precedence.

## Architecture

| Path | Purpose |
| --- | --- |
| `frontend/` | React + Vite + Tailwind + shadcn/ui dashboard |
| `frontend/src/lib/api.js` | Browser API adapter, retries, and response mapping |
| `backend/server.py` | FastAPI job app; routes live under `/api` |
| `backend/orchestrator.py`, `backend/scheduler.py` | Step execution, lifecycle controls, and single-worker queue |
| `backend/repos.py`, `backend/git_*` | Local repositories, commits, snapshots, and structured diffs |
| `backend/github_*`, `backend/notif*` | GitHub integration and notification delivery |
| `services/arena2api/` | Unmodified upstream gateway + Chrome/Firefox extensions (Git submodule) |
| `gateway/`, `run-gateway.sh` | Local configuration and standalone gateway launcher |
| `backend/tests/`, `frontend/tests/` | Local stand-ins and regression tests |
| `contracts.md` | Endpoint contracts, data shapes, and integration behaviour |
| `test_result.md` | Historical test reports and issue tracker |
| `run.sh` | Local app launcher; external gateway, `GATEWAY=1`, or `STUB=1` |
| `setup.sh` | Production-style Ubuntu provisioning; optional `GATEWAY=1` service |
| `test-integration.sh`, `compose.integration.yml` | Disposable MongoDB-backed test stack, never production app data |
| `.github/workflows/integration.yml` | The same local-only regression on a MongoDB-enabled CI runner |

Runtime repositories live in `data/repos/<job_id>` by default (`data/` is
Git-ignored). Keep `R2A_DATA_DIR` **outside `backend/`** so generated Python files
cannot trigger the backend's development reloader.

## Local development

### Prerequisites

- Node.js **22.12+** and Yarn Classic **1.22** for the dashboard.
- Python **3.11+**, virtualenv support, and Git on `PATH`.
- A running MongoDB instance for the job backend (Ubuntu setup installs MongoDB 8.0).
- For real generation: an authorised Arena session and the upstream browser
  extension, or another reachable OpenAI-compatible gateway.

The **standalone gateway does not need MongoDB or Node.js**. Its minimal launch
instructions are in [docs/arena2api.md](docs/arena2api.md).

From the repository root:

```bash
git submodule update --init --recursive services/arena2api
python3 -m venv venv
./venv/bin/python -m pip install -r backend/requirements-gateway.txt
(cd frontend && yarn install --frozen-lockfile)
cp backend/.env.example backend/.env
./venv/bin/python scripts/ensure_secret_key.py backend/.env  # optional: GitHub key
```

The combined requirements retain the application's exact dependency pins and
satisfy the pinned upstream requirements. External-gateway/stub-only users can
install `backend/requirements.txt` instead.

Edit `backend/.env` before starting:

| Variable | Purpose |
| --- | --- |
| `MONGO_URL` | MongoDB connection URI |
| `DB_NAME` | Database name; replace `your_db_name` with a dedicated name |
| `ARENA2API_URL` | Server-reachable gateway base URL, without `/v1` |
| `ARENA2API_MODEL` | Model identifier returned by that gateway's `/v1/models` |
| `ARENA_STEP_DELAY_SECONDS` | Initial delay between steps |
| `ARENA_REQUEST_TIMEOUT_SECONDS` | Initial gateway request timeout |
| `CORS_ORIGINS` | Allowed browser origins, comma-separated; restrict outside local development |
| `ARENA2API_API_KEY` | **Optional** HTTP Bearer key; match `GATEWAY_API_KEY` in `gateway/.env` |

The original seven variables are required; the API key is optional. The delay
and timeout seed persisted settings; later changes can be made in the dashboard.
The key stays server-side, outside public settings and job configuration, and is
sent only to the **exact configured `ARENA2API_URL`** (ignoring outer whitespace
and trailing slashes). URL aliases, ports, paths and other overrides do not get
it. See [`backend/.env.example`](backend/.env.example) for other optional settings.

### Choose a gateway mode

| Command | Behaviour |
| --- | --- |
| `GATEWAY=1 ./run.sh` | Start the included real gateway, job backend, and frontend |
| `STUB=1 ./run.sh` | Start the canned test gateway, job backend, and frontend |
| `./run.sh` | Start the app only; use an already-running gateway |

For the **included real gateway**:

```bash
cp gateway/.env.example gateway/.env  # optional; loopback defaults work without it
./run-gateway.sh --check             # validates the pin/import/config; no listener or Arena call
GATEWAY=1 ./run.sh
```

Install/connect the extension manually using the [gateway guide](docs/arena2api.md).
A healthy HTTP server is not proof of an active Arena session. Use a model from
`/v1/models`, not the `waiting-for-extension` placeholder. Existing saved settings
are not rewritten when `.env` changes; update/reset them in the dashboard.

For a **simulated run with no real Arena requests**:

```bash
# MongoDB must already be running. Keep ARENA2API_URL=http://localhost:9090.
STUB=1 ./run.sh
```

`STUB` and `GATEWAY` must be `0` or `1` and cannot both be enabled. The launcher
starts Vite at `http://127.0.0.1:5173` and the job API at
`http://127.0.0.1:8001/api`; the selected gateway defaults to port 9090. Ctrl+C or
a service exit tears down the launched stack. The stand-in returns canned files,
not Arena-generated code; diagnostic models include `stub-slow`, `stub-503`, and
`stub-503-at-3`.

Development browser requests stay **same-origin**: the launcher sets
`VITE_BACKEND_URL` empty, and Vite proxies `/api` to `http://127.0.0.1:8001`.
`R2A_BACKEND_TARGET` can override that target **server-side**. For a remote/live
preview, use `R2A_DEV_HOST=0.0.0.0`; keep access restricted. Behind Caddy, leave
`VITE_BACKEND_URL` empty too. Never point a remote browser at `localhost` to reach
a backend on another machine.

### Optional GitHub setup

To connect GitHub from Settings, the backend needs a Fernet encryption key in
`R2A_SECRET_KEY` in `backend/.env`. **`./setup.sh` generates it automatically** when
it is missing or empty and never overwrites an existing one; the key is never
printed and the file is set to mode 600. Without `setup.sh` (for example local
development), run only that step:

```bash
./venv/bin/python scripts/ensure_secret_key.py backend/.env
# tests (temp dirs only): ./venv/bin/python scripts/test_ensure_secret_key.py
```

Keep the key private and stable, and back up `backend/.env`. Losing or changing it
makes stored tokens unreadable and requires reconnecting GitHub. GitHub tokens
are encrypted at rest and are never returned by the API. Optional settings:

- `GITHUB_OAUTH_CLIENT_ID`: OAuth app client ID with device flow enabled. It can
  also be saved in Settings.
- `R2A_GITHUB_TOKEN`: server-side token override; disables UI connect/disconnect.
- `R2A_GITHUB_POLL_SECONDS`: branch/PR/CI polling interval (default 60, minimum 30).
- `R2A_DATA_DIR`: runtime storage location (default `<repository root>/data`).

Keep tokens and encryption keys out of Git. GitHub connection credentials are
**not application login/authentication**: the app has no user authentication.

## Checks and tests

```bash
# Frontend static checks, production bundle, and real Vite proxy/local-fixture smoke
(cd frontend && yarn lint && yarn build)
./venv/bin/python frontend/tests/dev_proxy.py

# Python dependency consistency and source/shell syntax
./venv/bin/python -m pip check
./venv/bin/python -m compileall -q backend gateway frontend/tests
bash -n run.sh run-gateway.sh setup.sh

# Test-only Python dependencies
./venv/bin/python -m pip install -r backend/requirements-dev.txt

# Self-contained checks (no MongoDB or real Arena/GitHub requests)
./venv/bin/python backend/tests/test_core.py
./venv/bin/python backend/tests/test_github_client.py
./venv/bin/python backend/tests/test_gateway.py
./venv/bin/python backend/tests/test_integration_support.py
```

Gateway checks require the initialised submodule and combined requirements. They
exercise the actual upstream app and OpenAI SDK using synthetic extension data
and an in-memory Arena transport, plus a short-lived standalone HTTP listener.
They do **not** connect a real browser session or send traffic to Arena.

### MongoDB-backed validation gate

A disposable real-MongoDB test path and CI workflow are now prepared:

```bash
# Requires Docker Engine + Compose v2; does not use the application's .env/data.
./test-integration.sh
# Or just the new real-backend/SDK/gateway job pipeline plus self-contained checks:
./test-integration.sh --pipeline-only
```

The launcher allocates a loopback-only random MongoDB port and temporary storage,
runs controlled local providers, and removes its Compose project on exit. You can
instead supply an explicit disposable loopback `TEST_MONGO_URL`. See the
[validation guide](docs/integration-validation.md) for setup, coverage and safety.
The persistence gate has passed against a real throwaway MongoDB 8.0 supplied via
`TEST_MONGO_URL` (2026-10-03); the Docker Compose path and the CI workflow itself
have not been executed yet.

Database integration suites also can be run individually with MongoDB and a local
arena stand-in. Start the
stand-in in a separate terminal from `backend/`:

```bash
../venv/bin/python -m uvicorn tests.arena_stub:app --host 127.0.0.1 --port 9090
```

Then, from the repository root:

```bash
export TEST_STUB_URL=http://127.0.0.1:9090
./venv/bin/python backend/tests/test_deletion.py
./venv/bin/python backend/tests/test_notifications.py
./venv/bin/python backend/tests/test_git.py
./venv/bin/python backend/tests/test_github.py
./venv/bin/python backend/tests/test_reload_isolation.py
```

These suites start private backends against throwaway databases. GitHub tests use
`backend/tests/github_stub.py` and local bare repositories, not github.com.
Notification tests deliver only to local receivers. Older API/queue/history
scripts target `TEST_BASE_URL` and include destructive cleanup; **never run them
against a database containing jobs you want to keep**. Read each script's setup
before use.

For a desktop/mobile smoke check of the **production frontend with in-memory API
fixtures** (no MongoDB required):

```bash
./venv/bin/python -m playwright install chromium
(cd frontend && yarn build)
./venv/bin/python frontend/tests/smoke.py
# Or: ./venv/bin/python frontend/tests/smoke.py --browser /path/to/chromium
```

This checks navigation, validation, settings controls, and empty states, using the
real Python roadmap parser for previews. It does not test persisted jobs. The
older browser lifecycle suites need a dedicated backend test stack as well.

A successful local/mocked run does not prove real Arena, GitHub, SMTP, or webhook
delivery works; those need separate, explicitly authorised smoke tests. Reports:
[baseline verification](docs/verification-2026-10-02.md) and
[initial gateway integration](docs/arena2api-integration-2026-10-02.md).

## Ubuntu deployment

On a dedicated Ubuntu server, inspect and run:

```bash
APP_NAME=roadmap2arena APP_PORT=8080 ./setup.sh
# Or include the pinned gateway as its own systemd service:
GATEWAY=1 APP_NAME=roadmap2arena APP_PORT=8080 ./setup.sh
```

Initialise the submodule first when enabling the gateway. The script installs
system packages, Node/Yarn, MongoDB, and Caddy; builds the frontend; creates a
single-worker systemd backend; and proxies `/api/*` to `127.0.0.1:8001`.
`GATEWAY=1` also creates `<APP_NAME>-gateway.service`, using `gateway/.env` and
loopback by default. Caddy does **not** expose its `/v1` or extension endpoints.
Browser extension setup and remote SSH forwarding remain manual.

The script changes system services and **replaces `/etc/caddy/Caddyfile`** (after
a backup), so do not run it casually on a shared machine. Configure the backend
and gateway environments before use and restart services after changes. Turning
`GATEWAY` off on a later run does not automatically remove an installed gateway
service; disable it explicitly if retiring it. Ubuntu provisioning has not been
executed in the verification sandbox.

Use exactly **one backend worker and one gateway worker**: the backend scheduler
and watcher, and the gateway's extension/session store, are process-local.
Production runs without `--reload`. The generated Caddy config is HTTP-only;
add HTTPS and authentication/network restrictions before exposing this app to
untrusted users. Back up MongoDB, runtime data, and the GitHub encryption key together.

**Upstream licensing/security:** arena2api has no declared license at the pinned
revision; this integration does not grant one. Review permissions before
redistribution. Its API key protects only models/chat, **not extension push or
status**, and upstream CORS is broad. Keep the gateway private even with a key;
read [docs/arena2api.md](docs/arena2api.md) before connecting real credentials.
