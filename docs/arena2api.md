# Included arena2api gateway

This is the initial integration of [flay-o/arena2api](https://github.com/flay-o/arena2api).
It provides the OpenAI-compatible gateway behind ROADMAP2ARENA. It does **not**
replace the application's FastAPI job API, MongoDB, queue, or Git storage.

```text
Dashboard --same-origin /api--> job backend :8001 --> gateway :9090 --> Arena
                                     |
                                  MongoDB
Browser extension --private HTTP------------------> gateway :9090
```

## Dependency and provenance

- Location: `services/arena2api/`, a Git submodule; upstream source is unmodified.
- Origin: `https://github.com/flay-o/arena2api.git`.
- Pinned commit: `259e27c2a96c8203cfe6d67140490b0db9f91543`.
- Local wrapper: `gateway/`; standalone entry point: `run-gateway.sh`.
- **License:** no declared upstream license was found at this revision. Keeping a
  Git reference preserves provenance; it does not imply a license grant. Review
  permissions before redistribution or incorporating it into a distributed product.

For existing checkouts, from the repository root:

```bash
git submodule update --init --recursive services/arena2api
```

For fresh clones, use Git's `--recurse-submodules` option. Normal setup must not
use `git submodule update --remote`: runtime checks require the reviewed pin, not
whatever upstream `main` currently contains. An intentional upgrade must update
the gitlink, `gateway.UPSTREAM_REVISION`, and these provenance notes together,
then re-review license/security and rerun the contract/SDK checks.

## 1. Run only the gateway

Requirements: Python 3.11+, virtualenv support, and Git. **No running MongoDB,
Node.js, frontend, or Arena account is needed to start/check the server.**
The combined Python requirements include backend packages, but starting this
service does not import or start the job backend.

```bash
# From the repository root; skip venv creation if it already exists.
git submodule update --init --recursive services/arena2api
python3 -m venv venv
./venv/bin/python -m pip install -r backend/requirements-gateway.txt

# Optional: configure the gateway; defaults work without this file.
cp gateway/.env.example gateway/.env
chmod 600 gateway/.env

./run-gateway.sh --check
./run-gateway.sh
```

`--check` verifies local HEAD, configuration, and the upstream import, without
starting a listener, fetching upstream, opening a browser, or calling Arena. It
prints whether a key is configured, **never its value**. No `.env` file is required
for the standalone gateway. Explicitly requested missing files are errors.

The default server is `http://127.0.0.1:9090`, one worker, no reload. Ctrl+C stops
it. In another terminal, a health-only check is safe without an Arena session:

```bash
curl http://127.0.0.1:9090/health
curl http://127.0.0.1:9090/v1/extension/status
```

Both endpoints are unauthenticated in upstream. `status: "ok"` means HTTP is up,
not that Arena is ready. Before a browser extension connects, expect an inactive
extension, the `waiting-for-extension` model placeholder, and HTTP 503 for chat.

### Configuration

Values come from optional `gateway/.env`, then process environment overrides;
`--host` and `--port` override both. Paths passed to the shell launcher are relative
to the repository root unless absolute. Example:

```bash
./run-gateway.sh --env-file gateway/.env --host 127.0.0.1 --port 9090
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | Bind host, not a URL; keep private |
| `GATEWAY_PORT` | `9090` | Integer 1–65535 |
| `GATEWAY_LOG_LEVEL` | `info` | Uvicorn log level; avoid debug/trace with real sessions |
| `GATEWAY_API_KEY` | empty | Optional printable-ASCII Bearer key for models/chat |

The wrapper maps these prefixed settings to upstream at import time and restores
ambient `API_KEY`, `PORT`, and `DEBUG` afterwards. Unrelated variables with those
names cannot silently configure this gateway. Ports, hosts, log levels, and key
characters are validated. Do not run multiple workers or reload: every process
would have its own extension/session store, and a restart loses that state.

### Optional HTTP API key

Generate a strong key locally, for example:

```bash
./venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep the result private. Put the **same value** in:

- `GATEWAY_API_KEY` in `gateway/.env` (gateway listener).
- `ARENA2API_API_KEY` in `backend/.env` (ROADMAP2ARENA's HTTP client).

These are gateway access keys, **not** Arena cookies, login tokens, or CAPTCHA
values. Real Arena session data is supplied by the browser extension, not `.env`.
Use restrictive file permissions (`chmod 600 backend/.env gateway/.env`) and keep
files out of Git. Never paste secrets into chat, issue reports, or debug logs.
Restart both services after changing their keys.

The app sends its configured key only when a job's base URL exactly matches
`ARENA2API_URL` after stripping outer whitespace/trailing slashes. For example,
`http://localhost:9090` and `http://127.0.0.1:9090` are **not** interchangeable for
credential forwarding. Other hosts, schemes, ports, paths, and job URL overrides
receive only the legacy SDK placeholder, not the configured secret. Use one
consistent URL in the environment and dashboard. Changing a saved URL does not
change which server is trusted with the key.

The key is excluded from public settings and persisted job config. Invalid header
characters fail at startup with a value-free error; configured key values echoed
in provider errors are redacted before those errors are stored/returned. Leaving
keys empty preserves the upstream unauthenticated models/chat mode.

**Important:** upstream protects **only** `GET /v1/models` and
`POST /v1/chat/completions` with this key. It does **not** authenticate health,
extension status, or extension push, and allows broad CORS. A key does not make
this service safe to expose publicly, or eliminate risks from untrusted local
processes/browser tabs. Keep the listener private and review the extension.

## 2. Connect the browser extension manually

This is an operator step, not performed by launchers, setup, or automated tests.
Use your own authorised session and comply with applicable upstream/platform terms.
Review the extension's cookie/storage permissions before enabling it.

### Chrome

1. Open `chrome://extensions/` and enable **Developer mode**.
2. Choose **Load unpacked** and select `services/arena2api/extension/`.
3. Open the extension popup; set **Server URL** to `http://127.0.0.1:9090` and save.
4. Open Arena using the popup or `https://arena.ai/?mode=direct`; log in normally
   if necessary and keep that tab open.
5. Wait for Server connected, Arena tab active, Auth cookie present, and Models > 0.

### Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Choose **Load Temporary Add-on** and select
   `services/arena2api/extension-firefox/manifest.json`.
3. Configure the same private Server URL and open/log in to Arena normally.
4. Keep the Arena tab open and check the popup's connection/auth/model indicators.
   Temporary add-ons need loading again after a browser restart.

If the gateway runs on another machine, the extension files must be available on
**your browser's machine** too. The browser's loopback address is not the server's.
Both pinned manifests allow HTTP loopback gateway URLs, not arbitrary HTTPS
preview domains. Prefer a private tunnel instead of widening permissions or
publishing extension endpoints.

### Remote server: SSH forwarding

On your browser's machine, keep an SSH tunnel open (substitute your server):

```bash
ssh -N -L 127.0.0.1:9090:127.0.0.1:9090 user@your-server
```

The remote gateway remains bound to loopback. The extension still uses
`http://127.0.0.1:9090` on your machine. ROADMAP2ARENA's backend, running on the
server, uses its own loopback gateway URL. Do not open port 9090 publicly or add
`/v1`/extension routes to Caddy. An Arena sandbox preview is not a drop-in
replacement for this loopback connection; it requires a suitable private tunnel.

### Inspect available models without a completion

Once connected, use `/v1/models`. With no gateway key:

```bash
curl http://127.0.0.1:9090/v1/models
```

For an authenticated model-list check without putting the key in command-line
arguments, run from the repository root on the gateway machine:

```bash
./venv/bin/python - <<'PY'
import httpx
from gateway.runtime import GatewayConfig
cfg = GatewayConfig.load()
headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
with httpx.Client(trust_env=False, timeout=10) as client:
    response = client.get(f"http://127.0.0.1:{cfg.port}/v1/models", headers=headers)
    response.raise_for_status()
    for model in response.json()["data"]:
        print(model["id"])
PY
```

This lists the gateway's discovered cache; it does not request model generation.
Choose an actual returned text-model ID (for example `GPT-4o` **if present**) for
`ARENA2API_MODEL` / dashboard settings. The placeholder is not a working model.

## 3. Run alongside ROADMAP2ARENA

Install frontend dependencies and configure MongoDB + `backend/.env` as described
in the root README. Then choose **one** approach:

```bash
GATEWAY=1 ./run.sh   # launcher owns gateway + app backend + frontend
```

Or, when the gateway is already running separately:

```bash
./run.sh           # app only; does not start a second gateway
```

Do not also run `STUB=1`, or start a second gateway on the same port. A custom
`GATEWAY_PORT` requires matching backend URL and extension Server URL changes.
An existing Mongo settings document is preserved: changing `.env` alone does not
update its saved URL/model; update them or reset defaults in the dashboard.

For dedicated Ubuntu hosts, `GATEWAY=1 ./setup.sh` opts into an independent
`<APP_NAME>-gateway.service`, with the backend ordered after/wanting it. The script
uses combined requirements, defaults to loopback, and leaves browser/tunnel setup
manual. Inspect first: it provisions system packages/services and replaces Caddy's
configuration. It was **syntax-checked, not executed** in this sandbox. A later
`GATEWAY=0` does not disable an already-installed unit; use systemd explicitly if
retiring it. Caddy serves the app and `/api`, not the gateway's private endpoints.

## Troubleshooting and limits

| Symptom | Check |
| --- | --- |
| Missing/mismatched upstream | Run the pinned submodule update command; do not follow upstream main automatically |
| HTTP healthy, but chat 503 | Keep the Arena tab open, check extension popup/status, refresh/push after gateway restart |
| Models placeholder/empty | Wait for the Arena page/model discovery; use a real returned model ID |
| HTTP 401 from models/chat | Match both keys; ensure the job URL exactly matches server-side `ARENA2API_URL` |
| HTTP 404 for model | Use a discovered text model; aliases/fuzzy matches are upstream behaviour, not guaranteed |
| Token pool empty / provider error | Browser session may need refreshing; upstream tokens expire and requests can hit rate limits |
| Remote browser cannot connect | Its localhost is local to the browser; check the private SSH tunnel and manifest permissions |

The gateway store is memory-only; extension activity expires after 120 seconds
without a push. Its outbound request timeout is upstream-controlled (300 seconds
in the pin), independent of the app's SDK timeout. Gateway HTTP success does not
prove generated files are correct; ROADMAP2ARENA does not execute or validate them.
Upstream extension logs can include an **auth-cookie prefix**: do not share raw
browser logs or enable verbose logging with real sessions.

## Verification

```bash
./run-gateway.sh --check
./venv/bin/python backend/tests/test_gateway.py
```

The suite uses the **actual pinned FastAPI app and real OpenAI SDK**, synthetic
extension payloads, ASGI clients, and mocked Arena HTTP streams. Mock transport
logs may name Arena's URL, but no DNS/network request is made to it. A separate
short-lived listener checks HTTP health, key enforcement and disconnected 503,
then terminates and verifies the port is closed. No MongoDB or real account is used.

See [the integration report](arena2api-integration-2026-10-02.md) for results and
remaining gates. A [disposable MongoDB-backed validation path and CI workflow](integration-validation.md)
are now prepared for the next persistence/lifecycle gate; their real Mongo/Docker
execution is still pending. Real browser pairing/generation and Ubuntu deployment
remain separately unverified.
