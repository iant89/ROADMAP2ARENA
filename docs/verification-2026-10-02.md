# Merged-build verification — 2026-10-02

**Result: PARTIAL VERIFICATION.** All available static, self-contained backend,
and fixture-backed browser checks pass. Database-backed regression is blocked;
this is not a complete end-to-end sign-off.

- Baseline: `cc63ec703f06971df0b27d619245ffa5a2984b68` (merged PR #10).
- Work performed on: `arena/01a0fbd7-roadmap2arena`.
- Final checks: 2026-10-02, approximately 05:19 EDT.
- Environment: Debian 12, Python 3.11.2, Node 22.22.3, Yarn 1.22.22.

## Results

| Check | Result | Notes |
| --- | --- | --- |
| Locked frontend dependency install | PASS | Same locked versions and integrities, fetched from npm's canonical tarball URLs; original `yarn.lock` restored unchanged |
| `yarn lint` | PASS with warnings | 0 errors, 19 existing warnings |
| `yarn build` | PASS with warning | Vite 8.3.2 production build; large-chunk warning remains |
| Python runtime/test dependency install | PASS | Repository's pinned requirements installed successfully |
| `python -m pip check` | PASS | No broken requirements |
| Python compilation: backend + frontend test scripts | PASS | Includes a Python 3.11 compatibility fix in an older browser test |
| Shell syntax: launch/setup + queue smoke scripts | PASS | `bash -n` |
| `backend/tests/test_core.py` | PASS | **43/43 tests**, no MongoDB or external HTTP |
| `backend/tests/test_github_client.py` | PASS | **9/9 tests**, mocked HTTP and local bare Git repositories |
| `frontend/tests/smoke.py` | PASS | **14/14 flows**: 7 desktop + 7 mobile, production assets and explicit API fixtures |
| Database-backed integration regression | BLOCKED | No MongoDB service/binary; installation endpoints unreachable |
| Real arena2api / GitHub / SMTP / webhooks | NOT RUN | Not authorised or configured for this verification |

The browser smoke check used Chromium 153.0.8010.0 from
`@sparticuz/chromium@153.0.0` in the sandbox cache, because the standard Playwright
browser download host was unreachable. Browser binaries and extracted libraries
are not part of the Git changes. The temporary static server was stopped after
the checks.

## What was verified

### Backend core and local Git

- Roadmap headings, checklists, descriptions, line endings, Unicode, and empty
  inputs; fenced examples are ignored correctly, including fence type/length,
  longer closing fences, and unterminated examples.
- Actual in-process `/api/roadmap/parse` responses: valid input, no steps,
  example-only input, invalid JSON, and wrong HTTP method.
- Malformed notification/email/GitHub request bodies return 422 without echoing
  the supplied dummy password/token in `input` or response text.
- Job defaults, trimming, override validation, and URL/model/roadmap rejection.
- Git branch validation, including rejection of `@{-1}`, `@`, `HEAD`, whitespace,
  unnormalised paths, and overlong names.
- Repository ownership/path guards and noninteractive Git configuration.
- First/subsequent prompts and conversation-history reconstruction using a
  mocked OpenAI client (no real gateway request).
- Real Git operations in temporary repositories: snapshots of committed content,
  include/exclude filters, byte/file-size limits, binary/symlink exclusion,
  structured diffs, renames, same-commit comparisons, and file-count truncation.
- Isolated test harness: current Python interpreter, optional/quoted `.env`
  settings, process-environment precedence, forced throwaway database names,
  effective Mongo URI for cleanup, temporary repo cleanup, and startup-failure
  cleanup. Process/database behaviour in these harness checks is mocked.
- Existing GitHub client tests: headers, retry policy, error mapping/redaction,
  ETags, watcher decisions, local pushes, and non-fast-forward refusal.

### Production frontend (API fixtures, not MongoDB)

At **1280×800** and **390×844**, each of these seven flows passed:

1. Initial load, defaults, accessible tab names, and tab visibility.
2. Sample roadmap and inline URL/model validation; invalid input never submits a job.
3. Fenced-example preview regression, using the real Python parser.
4. Tab navigation, empty queue/current/history states, and URL persistence on reload.
5. Settings validation/save/reset controls with in-memory responses, password input
   types, and the portable GitHub key-generation hint.
6. Notification empty state.
7. Unknown job deep link: a clean not-found message, no backend-lost warning.

No page exceptions or unexpected fixture requests occurred; browser API requests
were same-origin. External assets were blocked before dispatch. These checks do
**not** prove settings persistence in MongoDB, job execution, or GitHub connection.

## Bugs and maintenance addressed

### B-008 — fenced examples became roadmap steps

The initial core run failed **3 of 36 tests** on the merged baseline: fences were
tracked only after a step began, and any triple fence could toggle the state.
Headings or checkboxes inside a preamble example could therefore become runnable
steps, consume a later real step, or escape a longer/different fence.

`backend/roadmap_parser.py` now tracks fences throughout the document and accepts
only a matching closing delimiter of sufficient length with no info string.
Direct parser tests, in-process API tests, and both browser previews pass.

### Portable, safer verification setup

- Refreshed `README.md` to document the real backend/features, setup, test commands,
  optional GitHub settings, runtime storage, and deployment/security limits.
- Replaced `/app/venv` assumptions in isolated test process launchers and GitHub
  key-generation hints with the active interpreter or repository-relative command.
- The test harness defaults to local/inert integration URLs, blank GitHub
  credentials, an ephemeral encryption key, and temporary repositories. A caller
  can explicitly provide a local stub through `extra_env`; the throwaway database
  name and disabled legacy-data migration cannot be overridden accidentally.
- Fixed an older browser test's nested f-string quoting for Python 3.11; its full
  lifecycle flows were **not** run.

## Remaining warnings and blocked checks

The 19 lint warnings concern mixed component/helper exports and synchronous state
updates in effects. They were present before these changes and remain unchanged.
The production build still warns about chunks above 500 kB (main bundle roughly
634 kB; lazy DiffViewer bundle roughly 1,071 kB, before gzip).

MongoDB is absent, nothing listens on port 27017, and the local ping preflight
returns `ServerSelectionTimeoutError`. Attempts to download/install MongoDB from
`fastdl.mongodb.org` and `repo.mongodb.org` failed with connection/TLS errors.
The Debian package endpoint was also unavailable. Database-dependent tests were
not run against substitutes, production data, or a real user's integrations.

Still unverified in this checkout:

- Persisted job creation/execution, stop/resume/restart, queue concurrency,
  deletion, history/downloads, and live reloader isolation.
- Per-job Git commits and their MongoDB lifecycle association.
- Full GitHub API-stub suites (PAT/OAuth, encrypted storage, push/PR/auto-push,
  watcher notifications) and local SMTP/webhook integration suites.
- Real gateway behaviour, a real GitHub connection/push, browser notification
  permission prompts, and provider delivery.
- Actual Ubuntu provisioning/systemd/Caddy deployment (script inspected and
  syntax-checked only).

## Next validation gate

1. Make a dedicated MongoDB service available and configure `backend/.env` as
   described in the README; do not share production job data with older scripts.
2. Start the local arena stand-in and run the isolated database-backed suites.
3. Run lifecycle browser tests against that dedicated backend, including a job
   that writes Python files through several steps, stop/resume, and Git downloads.
4. Only after explicit approval, smoke-test the real arena2api gateway and a
   disposable GitHub repository. Do not put integration credentials in Git or
   verification reports.

No application jobs, production repositories, or real integration credentials
were created, deleted, or used during this verification.
