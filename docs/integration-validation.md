# MongoDB-backed integration validation

**Current status: harness/CI prepared; Mongo-backed execution is still blocked in
the Arena sandbox.** There is no MongoDB binary or Docker/Podman runtime here.
An alternate official-image registry route also failed TLS. No mock database is
substituted and no live Arena session is needed for these tests.

This is the next gate after [the initial gateway integration](arena2api-integration-2026-10-02.md).
It tests real job persistence and lifecycle without contacting real providers.
The [gateway operator guide](arena2api.md) remains the separate path for manually
connecting a real, authorised browser session.

## One command on a Docker-capable host

Requirements: Python 3.11+, Git, Docker Engine + Compose v2 supporting `up --wait`.
No Node/browser/MongoDB host installation or application `.env` is required.

```bash
# From the repository root:
git submodule update --init --recursive services/arena2api
python3 -m venv venv
./venv/bin/python -m pip install -r backend/requirements-gateway.txt -r backend/requirements-dev.txt

./test-integration.sh                 # self-contained + gateway pipeline + isolated DB regressions
# Or limit the DB run to the new gateway pipeline:
./test-integration.sh --pipeline-only
```

The launcher uses `compose.integration.yml` and a unique `r2a-test-*` project. It
creates **real MongoDB 8.0** on a random host port bound only to `127.0.0.1`. Data
and config storage are tmpfs; no host application data is mounted. Startup waits
for MongoDB's ping health check, then discovers the port. Exit, startup failure,
or a failed test tears down the Compose project and any anonymous volumes.

The image tracks MongoDB's `8.0` patch series; the driver prints the actual server
version at runtime. This is a validation service, not a production database.
Docker image pull availability and actual container lifecycle have **not** been
verified here; shell/YAML and mocked launcher cleanup checks have passed.

## Use an already-running disposable MongoDB instead

```bash
TEST_MONGO_URL=mongodb://127.0.0.1:27018/ ./test-integration.sh --pipeline-only
```

`TEST_MONGO_URL` must explicitly include one loopback host and port, without
credentials, database name, options, or fragments. Missing/remote/credentialed
URLs are refused. The driver never falls back to `MONGO_URL` or `backend/.env`.
Prefer a separate disposable instance; all backend harnesses use uniquely named
`roadmap2arena_test_*` databases and drop only those databases.

`R2A_TEST_PYTHON=/path/to/python` can select an installed Python environment.
The scripts do not install system packages, provision services, alter Caddy,
change the application's saved defaults, or connect a browser account.

## What the driver runs

1. Confirm a real MongoDB server is reachable and print its version.
2. Run self-contained core, gateway, mocked GitHub-client, and infrastructure checks.
3. Run `backend/tests/test_gateway_pipeline.py` (7 prepared cases), using:
   - a real MongoDB server;
   - the real FastAPI job backend and scheduler;
   - the real OpenAI SDK over localhost HTTP;
   - the actual pinned arena2api FastAPI gateway with a throwaway HTTP key;
   - **only** the gateway's outbound Arena HTTP replaced with `MockTransport`.
4. Unless `--pipeline-only` is selected, start the local canned arena stand-in and
   run isolated deletion, notifications, Git, GitHub-stub, and reload suites.

The new pipeline cases cover two-step persistence/chat history/artifacts/Git/ZIP,
rate-limit failure and resume, disconnected 503 and resume, trusted-URL credential
boundaries, stop/resume, queue pause/unpause, and database/repository deletion.
Each case owns a fresh throwaway DB and repo directory and checks their cleanup.
These cases are **implemented and compiled, not yet run against MongoDB**.

The reload positive-control suite runs in a **temporary source copy**: test source
changes and the default `data/` directory cannot affect the user's checkout or
normal repositories. Private `.env`, runtime data, bytecode and logs are excluded
from that copy. Historical scripts that target a direct backend or use broad
cleanup are intentionally not included in this driver.

## Fixture safety and scope

`backend/tests/gateway_fixture.py` is test-only and never invoked by app launchers.
Its CLI requires `R2A_TEST_GATEWAY=1`, binds loopback, and accepts a freshly generated
key through environment only. Extra `/_test/*` controls are protected by that key.
Its cookies/session/tokens/models are synthetic. No external Arena HTTP transport
exists in the fixture; URL-named HTTP logs are in-memory mocked requests.

This is **not** the canned `arena_stub` substituted for the real gateway: gateway
route/auth/protocol conversion and the backend SDK stay real. Conversely, it is
not proof of actual browser pairing, provider delivery, quotas, or Arena protocol
stability. Real-account smoke testing remains a separate operator task.

The driver supplies inert integration defaults; GitHub tests use their local stub
and bare repositories, and notification tests use local receivers. The fixture
and test harness must not be used as public or production services.

## GitHub Actions

`.github/workflows/integration.yml` provisions a MongoDB 8.0 service on an Ubuntu
runner, checks out the pinned submodule, installs app/gateway/test requirements,
and invokes the same `test-integration.sh` driver with an explicit loopback URI.
It requests only `contents: read`, does not persist checkout credentials, and pins
checkout/setup-python actions by commit SHA.

The workflow is available for affected pull requests, pushes to main/the current
session branch, or manual dispatch **after it is committed/pushed**. No workflow
run has been triggered, and no commit/push was performed in this task. YAML parsing
and action commit existence checks passed; execution is still pending.

## Executed sandbox checks — 2026-10-02

| Check | Result |
| --- | --- |
| `backend/tests/test_integration_support.py` | **20/20 PASS**: Mongo URI/environment safety, fixture contracts, owned-process cleanup, mocked Compose lifecycle, private reload source copy |
| Existing `test_core.py` / `test_gateway.py` / `test_github_client.py` | 43 / 36 / 9 passed after infrastructure addition |
| Pipeline Python compilation | Pass; not a Mongo-backed test pass |
| Compose/workflow YAML | Parsed; loopback/tmpfs and CI guard structure checked |
| Action SHA references | Both verified through GitHub metadata |
| Bash syntax, pip consistency, Git whitespace | Pass |
| `./test-integration.sh --pipeline-only` without Docker/explicit URI | Correctly refuses before starting a test stack |
| Real Docker/Mongo pipeline and CI run | **BLOCKED / NOT RUN** |
| Real Arena browser/account/provider delivery | **NOT RUN** |

See `test_result.md` for the final infrastructure case count and appended test log.
Earlier [baseline](verification-2026-10-02.md) and gateway reports remain historical;
this infrastructure work does not turn their incomplete persistence sign-off into
a pass. No runtime `.env`, jobs, production services, or real credentials were created.
