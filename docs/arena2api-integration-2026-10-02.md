# Initial arena2api integration — 2026-10-02

**Result: local integration checks PASS; complete application/provider sign-off
remains PARTIAL.** No real Arena session, credentials, model request, GitHub
account, or production deployment was used.

Working checkout: `arena/01a0fbd7-roadmap2arena`, based on `cc63ec7`.
The [earlier baseline report](verification-2026-10-02.md) is historical and has
not been rewritten. The checks below were rerun after the gateway/client changes.

## What was added

| Area | Change |
| --- | --- |
| Dependency | Unmodified `flay-o/arena2api` Git submodule at `services/arena2api`, pinned to `259e27c2a96c8203cfe6d67140490b0db9f91543` |
| Standalone runtime | `gateway/` + executable `run-gateway.sh`; optional prefixed environment, CLI host/port/env-file overrides, redacted `--check`, strict local revision check |
| Isolation | Separate FastAPI process; loopback :9090 default, one worker, no reload; no MongoDB or job-backend startup required |
| Python requirements | `backend/requirements-gateway.txt` combines existing exact app pins with pinned upstream requirements; no package/lock downgrade |
| App client | Optional server-only `ARENA2API_API_KEY`; forwarded only for the exact configured gateway base URL, never arbitrary job overrides/aliases/ports/paths |
| Secret handling | No key in public settings/job configuration; invalid header characters rejected without echo; provider error messages redact the configured value before persistence/return |
| Development | Opt-in `GATEWAY=1 ./run.sh`; external gateway remains default; mutually exclusive `STUB=1`; preflight/cleanup/service-exit handling |
| Browser API plumbing | Empty browser API base; Vite proxies `/api` server-side to loopback :8001 or `R2A_BACKEND_TARGET`; configurable preview host |
| Ubuntu provisioning | Opt-in independent gateway systemd unit, private new environment files owned by the service user, combined requirements, no gateway Caddy route; syntax-checked only |
| Operator docs | [Gateway setup/security](arena2api.md), refreshed root README and API contracts; manual Chrome/Firefox setup and private remote forwarding |

The existing FastAPI + MongoDB job backend remains intact. Projects/imported
repository context remains out of scope. Upstream source, extension manifests,
application dependency pins, frontend package manifest and Yarn lock are unchanged.

## Executed checks

| Check | Result | What it proves |
| --- | --- | --- |
| `python backend/tests/test_gateway.py` | **36/36 PASS** | Configuration/pin/import safety, credential boundary/redaction, actual upstream routes, real SDK compatibility, standalone HTTP listener |
| `python backend/tests/test_core.py` | **43/43 PASS** | Parser/artifacts/API validation, prompt/history fixtures, temporary committed Git repositories and mocked harness safety |
| `python backend/tests/test_github_client.py` | **9/9 PASS** | Mocked GitHub HTTP/error/watch behaviour and local bare-repo pushes; no real GitHub |
| `python frontend/tests/smoke.py --browser /tmp/chromium` | **14/14 PASS** | Built production UI, 7 desktop + 7 mobile flows, explicit API fixtures and real Python parser |
| `python frontend/tests/dev_proxy.py` | **5/5 PASS** | Actual Vite accepts preview Host; same-origin GET/POST/body/path/status forwarding; API target is server-only |
| `./run-gateway.sh --check` | **PASS** | Actual local pinned source imports; loopback :9090; key configured=false; no listener or provider call |
| `pip install -r backend/requirements-gateway.txt` | **PASS** | Combined requirements resolve against the existing exact pins (already installed) |
| `pip check` | **PASS** | No broken Python requirements |
| `yarn lint` | **PASS** | 0 errors, 19 existing warnings |
| `yarn build` | **PASS** | Production bundle builds; existing >500 kB warning remains |
| Python compileall | **PASS** | `backend`, `gateway`, and `frontend/tests` compile |
| Bash syntax | **PASS** | `run.sh`, `run-gateway.sh`, `setup.sh`, and both queue smoke shell scripts parse |
| Git checks | **PASS** | Root session branch preserved, submodule at expected revision with clean source, executable launchers, `git diff --check` |

Total backend checks: **88 passed** (36 gateway + 43 core + 9 GitHub client).
Frontend fixture coverage and dev-proxy checks are reported separately; they are
not database-backed end-to-end flows.

### Actual gateway / actual SDK, with no Arena network

The new suite dynamically imports the pinned **real upstream FastAPI app**. It
uses synthetic extension auth/cookies/tokens/model entries, and checks:

- HTTP health with an inactive extension and no outbound model request.
- Models/chat key enforcement, optional no-key mode, and redacted check output.
- Upstream's **unauthenticated** extension push/status behaviour (a security
  limitation, not a promised protection).
- Cached model discovery, expired extension / disconnected HTTP 503, unknown
  model 404, and malformed-JSON/empty-message 400 handling.
- Local Arena-protocol streams converted to nonstream OpenAI completion JSON and
  OpenAI SSE; simulated HTTP 429 propagation.
- A **real OpenAI SDK** `ArenaClient` talking to the gateway through ASGI transport:
  two completions, rolling user/assistant history, and named file extraction.
  Disconnected 503 is also observed through the actual SDK with no history added.
- Dummy keys never forwarded to untrusted URL overrides or leaked through public
  settings / provider error formatting, including plaintext truncation boundaries.
- A short-lived standalone listener, explicitly bound to `0.0.0.0` on a free test
  port, with only synthetic key/no extension. Health, 401, waiting model and 503
  are checked locally; normal termination and closed listener are verified.

Only the gateway's outbound HTTP namespace is replaced with an in-memory
`httpx.MockTransport`; the SDK and gateway routing/conversion stay real.
HTTP logs may name `https://arena.ai/...`, but these are **mock-transport calls**:
there is no DNS lookup, socket connection, or Arena traffic. SDK transport is
ASGI, not an external service. No real session data was read or pushed.

### Regression/test maintenance

The initial standalone-process assertion expected exit code 0. Recent Uvicorn
re-raises SIGTERM after shutdown, giving the normal `-15` exit status. The test now
accepts normal 0/SIGTERM and additionally verifies the port is closed; this was a
test expectation repair, not a runtime failure.

The direct GitHub-client test command initially failed because importing its
helpers requires the app's seven environment values. It now seeds inert defaults
and blanks the gateway key, making the mocked suite runnable without a real `.env`.
The rerun passes all 9 checks. Production startup requirements were not relaxed.

## Reproduction

From the repository root, after creating `venv` and installing frontend dependencies:

```bash
git submodule update --init --recursive services/arena2api
./venv/bin/python -m pip install -r backend/requirements-gateway.txt
./venv/bin/python -m pip install -r backend/requirements-dev.txt
./venv/bin/python -m pip check
./run-gateway.sh --check
./venv/bin/python backend/tests/test_gateway.py
./venv/bin/python backend/tests/test_core.py
./venv/bin/python backend/tests/test_github_client.py
(cd frontend && yarn lint && yarn build)
./venv/bin/python frontend/tests/dev_proxy.py
./venv/bin/python frontend/tests/smoke.py --browser /path/to/chromium
./venv/bin/python -m compileall -q backend gateway frontend/tests
bash -n run.sh run-gateway.sh setup.sh backend/tests/queue_smoke.sh backend/tests/queue_smoke_test_prefix.sh
git diff --check
```

In this sandbox the browser command used the previously prepared, ignored
Chromium 153 binary and bundled libraries:

```bash
LD_LIBRARY_PATH=/tmp/al2023/lib ./venv/bin/python frontend/tests/smoke.py --browser /tmp/chromium
```

Venv, node_modules, browser binaries, bytecode, build output, runtime/test data and
logs are not deliverables and remain out of Git. Owned HTTP/Vite/gateway test
servers were stopped. No runtime `.env` files were created in the repository.
No commit, push, system provisioning, or application/provider deployment occurred.

## Remaining gates and limitations

1. **MongoDB-backed regression remains blocked.** No MongoDB service/binary is
   available here. Previously blocked download/install endpoints were not retried.
   Job persistence, lifecycle/queue, deletion, Git association, live reload,
   notification and full stubbed-GitHub integration suites were not rerun.
2. **Full `GATEWAY=1 ./run.sh` app lifecycle is not signed off.** Mode validation,
   gateway preflight/standalone runtime and actual Vite proxy are checked, but the
   combined Mongo-backed stack needs its own regression.
3. **Real browser pairing and generation are untested.** The extensions were
   inspected, not installed/run in a real account. Model availability, session
   expiry, provider quotas/rate limits and live Arena protocol delivery require
   a separately authorised operator smoke test.
4. **Ubuntu provisioning/systemd/Caddy changes are not deployment-tested.** Bash
   syntax passed; no packages, firewall, service units or Caddy files were applied.
5. **Not public-ready.** Upstream only authenticates models/chat; health and
   extension endpoints are open and CORS is broad. Keep loopback/private even with
   a key. Upstream extension logs can reveal an auth-cookie prefix.
6. **Licensing unresolved.** The pinned upstream has no declared license; a Git
   submodule preserves provenance, not a permission grant. Review before redistribution.

Next practical gate: configure MongoDB and manually pair the private gateway with
an authorised browser session, then validate a small job before wider use.
