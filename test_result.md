# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Agent: backend
Date: 2026-10-02 01:45 ET
Built or changed: TWO stacked PRs, test both in the git worktree /workspace/r2a-wt on branch feat/github-integration (it contains C). /app stays on feat/notifications - do not switch it.
(C) feat/git-integration (PR #8): every job has a local git repo at <R2A_DATA_DIR>/repos/<job_id> (branch main); each completed step is committed as "Step N: <title>" by ROADMAP2ARENA (sha stored as step.commit_sha). Resume continues the same repo, restart/clone get new repos, deleting a job removes its repo, older jobs can get a repo via POST /git/init. Unsafe paths (.., .git segments, symlinked parents, file/dir clashes) are skipped and logged; git failures never fail a job. Jobs gain project_id (null) and repo_id; new "repos" collection with an owner.
(D) feat/github-integration: connect GitHub with EITHER a personal access token OR OAuth (device flow) - mutually exclusive, enforced server-side (409). The token (both kinds) is stored Fernet-encrypted (integrations/_id "github", field token_enc) with R2A_SECRET_KEY from backend/.env and never returned; no/invalid key -> 409 with a hint; a changed key -> token_error, reconnect; optional R2A_GITHUB_TOKEN in .env overrides (UI connect/disconnect then 409). Push a job's repo to a new or existing repo + branch (default r2a/job-<id8>), optional PR (existing repos only), result links, auto-push on completion (setting). Never force-pushes. git gets the token only as http.<host>.extraHeader via GIT_CONFIG_COUNT env vars (never argv/URL/.git/config); all git output redacted. Raw httpx REST (X-GitHub-Api-Version 2026-03-10, GET retry once on 5xx/network, POST never) with error mapping 401/403(+permission hint)/404/409/422/429(+Retry-After)/451/502/504. Polling watcher: each push upserts a github_watches doc; an in-process loop (every R2A_GITHUB_POLL_SECONDS, default 60, min 30; also after a push and on POST /api/github/poll) uses ETag conditional GETs on branch, PR (`merged`), combined status and check-runs (fallback actions/runs on 403/404) and emits notifications github_pushed, github_pr_opened, github_pr_merged, github_pr_closed, github_checks_passed, github_checks_failed (payload gains `link`).
Key endpoints or flows: (C) GET /api/jobs/{id}/git; POST /api/jobs/{id}/git/init; GET /api/jobs/{id}/git/commits/{sha} (now with structured `diff`); GET /api/jobs/{id}/git/compare?head=&base= (D); GET /api/jobs/{id}/git/download?format=zip|bundle. (D) GET /api/github; PUT /api/github/token {token}; DELETE /api/github; PUT /api/github/settings {auto_push:{enabled,private}, oauth_client_id}; POST /api/github/oauth/start | /poll | /cancel; GET /api/github/repos?q=; GET /api/jobs/{id}/github; POST /api/jobs/{id}/github/push {mode new|existing, repo_name, private, repo_full_name, branch, open_pr, pr_base, pr_title, pr_body}; GET /api/github/watches; POST /api/github/poll; GET /api/notifications (new `link`), notification settings with the six github_* events. Details and error codes: contracts.md ("Git history" and "GitHub" sections). Also regression of earlier endpoints.
Data: real backend (isolated instances only)
Features present: sessions no, auth no (GitHub token/OAuth is an integration credential), integrations: arena2api via local stand-in; GitHub ONLY via the local stub
Retest: F-006 (frontend tester, CRITICAL: a job writing app/main.py restarted the --reload backend -> "Job interrupted by server restart"; job repos now live in R2A_DATA_DIR, default <repo root>/data outside backend/, legacy backend/data/repos migrated on startup; reload flags in deploy/supervisor/backend.conf + run.sh watch only backend/ minus tests/, data/, *.git*; covered by backend/tests/test_reload_isolation.py which runs the real reloader with those flags), B-006 (branch/pr_base "@{-1}", "@", "main@{u}", "HEAD", "a//b" -> 422; valid_branch no longer depends on the backend cwd), B-007 (oauth/poll: 200 "connected" only after an OAuth sign-in, 409 when a PAT/env token is connected). Harness: isolated_server sets TEST_DIRECT_URL + R2A_TEST_ISOLATED, test_backend_api skips the :8080 proxy check when isolated (SKIP line).
Test accounts: GitHub stub tokens (fake): ghp_validtoken000000000000000000000000 (r2a-tester, repo scope), github_pat_finegrained0000000000000000, ghp_readonly0000000000000000000000000000 (no repo scope); OAuth stub client id Iv1.testclient0000
Notes:
- NEVER use Ian's real GitHub account, real tokens, github.com or api.github.com. Run backend/tests/github_stub.py (local GitHub API + device-flow stand-in, "repos" are bare git repos under --root) and start the backend with GITHUB_API_URL=<stub>, GITHUB_OAUTH_URL=<stub>, R2A_GITHUB_ALLOW_FILE_REMOTES=1 (local push targets; without it only https://github.com clone URLs are accepted), R2A_GITHUB_TOKEN="" and R2A_SECRET_KEY=<a fresh key: /app/venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"> (cryptography is installed in /app/venv). Stub controls for the watcher: POST <stub>/_stub/pr {full_name, number, action: merge|close}, POST <stub>/_stub/ci {sha, statuses, checks, runs, checks_forbidden}, POST <stub>/_stub/fail {path, status, times, message, headers}, POST <stub>/_stub/rate?remaining=N; GET <stub>/_stub/requests shows status (304s) and api_version. Approve/deny/expire device codes with POST <stub>/_stub/device?user_code=..&action=approve|deny|expire|slow_down.
- Use backend/tests/isolated_server.py from the WORKTREE (/workspace/r2a-wt/backend/tests) with extra_env={"R2A_DATA_DIR": <temp dir>, ...}; it serves the worktree code on a free port with a throwaway DB. Do not use :8001 (that is /app, feat/notifications). backend/tests/test_github.py shows the full setup.
- The shared arena stand-in on :9090 keeps stub-503-once-at-N state per process - prefer a private stub (uvicorn tests.arena_stub:app on a free port, from the worktree backend dir; suites honour TEST_STUB_URL).
- Worth probing: path safety of artifacts in commits, concurrent steps/downloads/init on one repo, resume/restart/clone repo semantics, delete removing repos, token never in any response/log/argv, PAT/OAuth exclusivity incl. while a device code is pending, poll rate limiting (never faster than interval), non-fast-forward push -> 409 and the remote branch unchanged, PR errors (unrelated history, missing base, existing PR reused), auto-push failure logged while the job stays done, token encrypted in Mongo (never plaintext) and the key-missing/changed paths, extraHeader only via env (inspect the git process env/argv), redaction of git output, GET retry vs POST no-retry, rate-limit 429 + Retry-After, watcher: no duplicate notifications across polls, merged vs closed, check-runs 403 -> actions/runs fallback, 304s on unchanged resources, rate-low pause, watches removed on job delete, polling while disconnected is a no-op.
- D also adds: structured diffs (backend/git_diff.py) + compare endpoint and the DiffViewer UI (split/unified, syntax + word highlighting, file list with +/- counts) in the Git tab and "Compare steps"; icon pass on action buttons. Worth probing compare with base==head, unknown/short shas, binary files, huge diffs (caps/truncated), renames.
- New tests: backend/tests/test_git.py (16), backend/tests/test_github.py (32), backend/tests/test_github_client.py (9, respx + local bare repo), backend/tests/github_stub.py. Playwright on an isolated stack: git tab + DiffViewer 27/27, GitHub 35/35.
- The poller runs inside the backend process: uvicorn must stay at --workers 1.
- Put test scripts in /workspace/r2a-wt/backend/tests/.

## Issue Tracker

| ID | Severity | Area | Summary | Status |
|----|----------|------|---------|--------|
| B-001 | CRITICAL | GET /api/jobs/{id}/steps/{index} | index > 2^63-1 (e.g. 99999999999999999999999) returns 500; OverflowError from pymongo in server.py get_step | VERIFIED |
| B-002 | MAJOR | POST /api/jobs | One-job-at-a-time rule is racy: 5 concurrent POSTs all returned 201 and 5 jobs ran at once (expected one 201 + four 409); check-then-insert in server.py create_job | VERIFIED |
| F-001 | MAJOR | Frontend StartForm validation | Empty/whitespace model only disables Start job silently; no error message explains why (expected a clear error) | VERIFIED |
| F-002 | MINOR | Frontend App URL state | Starting/opening a job does not put ?job=<id> in the URL, so a reload drops back to the empty form (job still reachable via Recent jobs) | SKIPPED |
| B-003 | CRITICAL | POST /api/jobs/{id}/stop | Concurrent stop calls on a running job (4 parallel) all return 200 with status "running"; job and step stay "running" with no task (no stop log line), blocking every new job until stop is called again. Repeat cancel() in orchestrator.stop interrupts the CancelledError handler/mark_stopped in run_job | VERIFIED |
| F-003 | MAJOR | Frontend HistoryDetail (mobile 390px) | Job detail tab bar (Transcript/Files/Steps/Log) is not wrapped in a horizontal scroller, so the history detail page is ~474px wide at 390px: page scrolls sideways, Log tab starts off-screen and the touch-emulated click on it failed (intercepted) | VERIFIED |
| F-004 | MINOR | Frontend useJob / history deep link | ?tab=history&job=<unknown id> shows a "Lost contact with the backend ... retrying automatically" toast for a plain 404 (nothing is retried; the inline "Could not load job ... not found" message is correct) | VERIFIED |
| F-005 | MINOR | Frontend api.listJobs / HistoryPanel | listJobs() drops queue_position from GET /api/jobs, so the history list never shows the "#N in queue" hint for queued jobs | VERIFIED |
| V-001 | MAJOR | Frontend App main tab bar (390px) | Axiom Vision: "Current job" clipped, Job history and Settings off-screen with no cue. Below sm the tabs are icon-only (labels hidden sm:inline, aria-label on each), triggers flex-1, TabsList w-full - all 5 fit | VERIFIED |
| V-002 | MAJOR | Frontend sub-tab bars (390px) | Axiom Vision: HistoryDetail (Transcript/Files/Steps/Log) and Current job (Artifacts/Transcript/Log) bars clipped. Mobile: grid w-full grid-cols-4 / grid-cols-3, triggers px-2 text-xs, count badges hidden sm:inline-flex; desktop unchanged | VERIFIED |
| V-003 | MINOR | Frontend HeaderBar | Axiom Vision: running job name can crowd the progress bar. Name shrink-0 max-w-[16rem] truncate (title tooltip), progress bar flex-1 min-w-[8rem] | FIXED |
| V-004 | MINOR | Frontend HistoryDetail / JobView | Axiom Vision: duplicate Download ZIP when the success banner shows. Header button hidden for done jobs; the banner button carries data-testid download-zip-button and the Packing state | VERIFIED |
| V-005 | MINOR | Frontend HistoryPanel filters | Axiom Vision: status filter chips wrap to 2-3 rows. Single row flex flex-nowrap gap-1.5 overflow-x-auto pb-1, chips shrink-0 | VERIFIED |
| V-006 | MINOR | Frontend HeaderBar (mobile) | Axiom Vision: tall mobile header. Queued count moved into the status row (icon + number below sm), subtitle hidden sm:block, "steps" word hidden below sm; header 96px at 390px | VERIFIED |
| V-007 | MINOR | Frontend Clone form / StartForm | Axiom Vision: mixed input/textarea sizes and fonts. All four fields font-mono text-base sm:text-sm (16px mobile, 14px desktop) | VERIFIED |
| V-008 | MINOR | Frontend SettingsTab | Axiom Vision: inconsistent ".env default" hint position. Label rows flex flex-wrap items-baseline justify-between gap-x-2, hint min-w-0 truncate (same pattern in StartForm field labels) | FIXED |
| B-004 | MAJOR | PUT /api/notifications/settings, POST /api/notifications/test/email | A non-object JSON body (e.g. a list or string) that contains an SMTP password gets a FastAPI 422 dict_type error that echoes the submitted body in "input", including the password in clear text. The stored password is never returned. Cause: `body: dict = Body(...)` in notification_routes.py uses default request validation | VERIFIED |
| B-005 | MINOR | DELETE /api/notifications/settings | Returns 404 "Notification settings not found" instead of 405: DELETE /api/notifications/{notif_id} captures "settings" (also POST /api/notifications/settings/read -> 404). Harmless (no data changed) | VERIFIED |
| B-006 | MAJOR | POST /api/jobs/{id}/github/push | branch "@{-1}" / "@{-2}" passes validation and the push fails with 502 "git push failed ... invalid refspec 'HEAD:refs/heads/@{-1}'" instead of 422. git_cli.valid_branch runs `git check-ref-format --branch` with cwd=None, so git expands @{-N} against the backend's own working-directory repo (it printed this worktree's previous branches) and returns success | VERIFIED |
| B-007 | MINOR | POST /api/github/oauth/poll | With no device flow pending but GitHub already connected, it returns 200 {status: connected} instead of the contract's 409 "nothing pending". The code does this on purpose (github_integration.py oauth_poll); the contract table should document it, or the code should return 409 | FIXED |
| F-006 | CRITICAL | Live stack: job git repos (PR #8) + uvicorn --reload | Job repos live in /app/backend/data/repos, inside the folder the live backend watches with uvicorn --reload. When a later step rewrites an existing .py file (stub-slow step 3 rewrites app/main.py), StatReload restarts the backend and the running job ends as error "Job interrupted by server restart" (no Job finished/failed alert); the queue then moves on. Seen twice (R1 b39b3e82, ed7fc0d7). Fix: keep repos outside the watched dir or add --reload-exclude for data/ | OPEN |

## Test Log

### Backend test run - 2026-10-01 20:01 ET
Tester: backend testing agent
Scope: All 7 /api endpoints on http://127.0.0.1:8001 (+1 request via Caddy :8080): config, roadmap parse, job create/list/get, step detail, ZIP download; job lifecycle with local stub (gpt-4o, stub-503, stub-503-at-3, stub-slow, unreachable arena); response shape vs contracts.md, no _id, UUID4 ids, ISO-8601 Z timestamps; 422 validation (bad URL, empty/whitespace model, empty/no-step roadmap, wrong types, bad JSON); 404 unknown/malformed ids and step index; 405/404 routes; 409 second job and 409 download without artifacts; ZIP rules (unnamed blocks excluded, ../ skipped and logged, /abs stripped, latest version wins); unicode/emoji/long input; concurrent job creation. Suite: /app/backend/tests/test_backend_api.py (runner run_tests.py; pytest/requests not installed, httpx used). All test jobs deleted afterwards.
Result: FAIL
Passed: 18   Failed: 2
Failures:
- [B-001][CRITICAL] GET /api/jobs/{id}/steps/99999999999999999999999 - expected 404 (or 422) vs actual 500 Internal Server Error - index is an unbounded Python int passed to Mongo find_one in server.py get_step (OverflowError: MongoDB can only handle up to 8-byte ints)
- [B-002][MAJOR] POST /api/jobs (5 concurrent requests, stub-slow) - expected one 201 and four 409 vs actual five 201, five jobs running at once - server.py create_job checks orchestrator.is_running()/find_one then awaits inserts before orchestrator.start, so concurrent requests all pass the check
Retested: none (no Retest items or FIXED issues)
Not tested: backend restart mid-job ("interrupted by server restart") - restarting a running backend is not allowed for the tester; 500-entry log cap; real arena2api.
Severity note: CRITICAL here corresponds to BLOCKER in the protocol scale.

### Backend test run - 2026-10-01 20:04 ET
Tester: backend testing agent
Scope: Retest of B-001 and B-002, then full regression of all 7 /api endpoints (same suite as the previous run, plus a B-001 boundary test; concurrency test raised to 6 parallel POSTs and now also checks that only one job is running). Suite: /app/backend/tests/test_backend_api.py, runner run_tests.py. All test jobs deleted afterwards.
Result: PASS
Passed: 21   Failed: 0
Failures: none
Retested:
- B-001 VERIFIED: GET /api/jobs/{id}/steps/{index} returns 200 for index 1 and 404 for 0, 100000, 100001, 2^63-1, 2^63, 2^64, 99999999999999999999999 and -(2^63+1); no 500s.
- B-002 VERIFIED: 6 concurrent POST /api/jobs (stub-slow) returned exactly one 201 and five 409; only that one job showed as running, and it finished done.
Not tested: backend restart mid-job ("interrupted by server restart"); 500-entry log cap; real arena2api.
Note: the backend runs with uvicorn --reload watching /app/backend including tests/, so editing a test file reloaded the backend (StatReload in the log). No job was running at the time, but such a reload would interrupt a running job.


### Frontend test run - 2026-10-01 20:10 ET
Tester: frontend testing agent
Scope: Dashboard at http://localhost:8080 in headless Chromium (1440x900) against the real backend and the local arena2api stand-in (:9090): initial load/config defaults and empty states, sample roadmap preview, form validation, full job run (gpt-4o and stub-slow) with live checklist/log/progress, Artifacts tab, Transcript tab, Log tab, ZIP download, deep link + reload, Recent jobs sheet, error job (stub-503-at-3), unknown route and bad job ids, reload after starting a job. Script: /app/frontend/tests/run_flows.py (one function per flow). No route interception needed (all /api calls go to localhost:8080/api). Test jobs (5, titled test_ue68d...) were removed from Mongo by id afterwards (no delete API/UI exists).
Result: FAIL
Flows:
load_page - PASS
sample_preview - PASS
validation - FAIL
run_job - PASS
artifacts - PASS
transcript - PASS
log - PASS
download_zip - PASS
deep_link_reload - PASS
recent_jobs - PASS
error_job - PASS
url_after_start - PASS
routes - PASS
reload_after_start - FAIL
Failures:
- [F-001][MAJOR] validation, empty or whitespace-only model - expected a clear error instead of starting vs actual Start job button just turns disabled with no message saying why (bad URL scheme correctly shows a "Could not start job" toast with the 422 detail) - StartForm.jsx startDisabled checks !form.model.trim() but no inline/field error is rendered. Screenshot /tmp/screenshots/validation.png
- [F-002][MINOR] reload_after_start, reload after starting a job - expected the current job to stay open after reload vs actual page returns to empty "New job" form; URL stays "/" without ?job=<id> (job is still in Recent jobs, no data lost) - App.jsx handleStart/handlePickJob never write ?job=<id> into the URL (history.replaceState). Screenshot /tmp/screenshots/reload_after_start.png
Console errors: "Failed to load resource: 422" x3 (expected 422s: POST /api/roadmap/parse when the roadmap has no steps, and POST /api/jobs with ftp:// URL); "Failed to load resource: 404" x3 (expected: /?job=not-a-real-id and unknown UUID deep links). No page exceptions, no React warnings.
Network failures: None (no requestfailed, no CORS errors, no wrong-host /api calls; only external requests are Google Fonts). Only /api >=400 responses were the expected 422/404s above.
Retested: none (no Retest items, no FIXED issues)
Not tested: tablet/mobile layout (excluded by the requester for this run); backend restart mid-job; real arena2api. Note: with the instant stub replies (gpt-4o) a step is "running" for under one 1.5 s poll, so the running state was verified with stub-slow.

### Frontend test run - 2026-10-01 20:23 ET
Tester: frontend testing agent
Scope: Retest of F-001 (inline validation for model and arena2api URL) plus a quick regression of run_job (sample roadmap, stub-slow so the running state is visible), artifacts, download_zip and error_job (stub-503-at-3) on http://localhost:8080 in headless Chromium (1440x900) with the real backend and the :9090 stand-in. Script: /app/frontend/tests/run_flows.py (new flow validation_inline). No route interception needed. F-002 not retested (SKIPPED). Test jobs (10, titled test_1duks...) were removed from Mongo by id afterwards (no delete API/UI exists).
Result: PASS
Flows:
validation_inline - PASS
load_page - PASS
run_job - PASS
artifacts - PASS
download_zip - PASS
error_job - PASS
Failures: none
Console errors: None (only Vite connecting/connected debug messages).
Network failures: None (no requestfailed, no CORS errors, no /api responses >=400 in the final runs, no wrong-host /api calls; only external requests are Google Fonts).
Retested:
- F-001 VERIFIED: no messages on first load; empty and whitespace-only model show "Model is required" under the field (aria-invalid=true) and disable Start job; ftp://localhost:9090 and localhost:9090 show "Use an http:// or https:// URL"; empty URL shows "arena2api base URL is required"; both messages can show at once; valid values clear both and re-enable Start; no POST /api/jobs was sent and status stayed Idle.
Not tested: F-002 (SKIPPED by request); transcript, log, deep link, recent jobs, routes (not in this regression scope); tablet/mobile; backend restart mid-job; real arena2api.
Note: early run_job attempts failed because the Vite dev server sent {"type":"full-reload"} over its websocket when files under /app/frontend changed during a run (the tester's own state file and script edits in /app/frontend/tests). This came from the test harness, not the app. The state file was moved to /tmp, and run_job then passed with no reloads. Tester jobs left orphaned by those reloads were included in the cleanup.

### Backend test run - 2026-10-01 20:47 ET
Tester: backend testing agent
Scope: feat/job-controls: POST /api/jobs/{id}/stop, /restart, /resume (happy paths, stopped_step/restarted_from set and persisted, state-transition 409s, 422 override validation, 404 unknown/malformed ids, 405 wrong method, abandoned slow request never flips step to done, earlier steps intact after stop and resume, history rebuild checked via "stub reply for user turn N" with stub-slow-at-2, stub-503-at-3 + model override and stub-503-once-at-2, stop between steps, concurrent restart/resume/create one-job rule, concurrent stops) + full regression of the existing API. New suite /app/backend/tests/test_job_controls.py (run directly with /app/venv/bin/python); regression via run_tests.py (Axiom's test_backend_api.py updates kept). Test files written before any job started; no edits under /app/backend while jobs ran. All test jobs deleted.
Result: FAIL
Passed: 36   Failed: 1
Failures:
- [B-003][CRITICAL] POST /api/jobs/{id}/stop x4 in parallel on a job mid-step (stub-slow-at-2) - expected job "stopped", stopped_step 2, one 200 (others 200/409) vs actual four 200s with body status "running", stopped_step null; job and step 2 still "running" after 6 s with no stop log line, so any_job_running() returns 409 for all new jobs until another stop - orchestrator.stop calls task.cancel() on every call; the 2nd cancel lands while run_job's CancelledError handler awaits mark_stopped, aborting it (reproduced twice)
Retested: none (no Retest items or FIXED issues)
Not tested: backend restart mid-job / resuming an "interrupted by server restart" job (restarting a running backend is not allowed for the tester); 500-entry log cap; real arena2api.

### Backend test run - 2026-10-01 20:56 ET
Tester: backend testing agent
Scope: Retest of B-003 (concurrent stop) on feat/job-controls, the new stop-after-finish 409 behaviour, then regression of both suites (test_job_controls.py 18 tests incl. cleanup, test_backend_api.py 21 tests). Test files were updated before any job started (concurrent-stop test now loops 3x with 4-6 parallel stops and starts a new job after each; new stop-vs-natural-finish race test; stub-503-once-at-N uses N from env ONCE_N, default 4). Backend confirmed healthy before running. All test jobs deleted.
Result: PASS
Passed: 39   Failed: 0
Failures: none
Retested:
- B-003 VERIFIED: 9 iterations over 3 runs (4, 5 and 6 parallel stops on a stub-slow-at-2 job mid-step 2). Each time the job ended "stopped" with stopped_step 2 and steps done/stopped/pending, with exactly one "Job stopped" log line. Every 200 body said status "stopped", and a new job started (201) right afterwards. Step 2 never flipped to done 5.5 s later.
- Stop vs natural finish (1-step stub-slow-at-1 job, 3 parallel stops at about 4.85-5.15 s): either 200 and the job ends "stopped" with one stop log line, or 409 for every call and the job ends "done" with no stop log line. Never a 500 and no job left running. A caller arriving just after a successful stop gets 409 "Job is stopped", which matches the contract.
Not tested: backend restart mid-job / resuming an "interrupted by server restart" job; 500-entry log cap; real arena2api.

### Backend test run - 2026-10-01 21:16 ET
Tester: backend testing agent
Scope: feat/tabs-and-queue. Queue: FIFO order with exactly one running job (polled running count during runs, no overlapping started/finished times, next job starts <2 s after the previous ends); move (up/down, position 1, beyond the end clamps, 0/-1/both/neither/bad direction/non-int/bad JSON -> 422, running job -> 409, unknown/malformed id -> 404); pause moves to the end, is idempotent, and a paused job moved to position 1 is still skipped; unpause with nothing running starts the job at once; DELETE -> cancelled, kept in history, never runs, repeat/running/unknown -> 409/409/404; stop/resume/restart on queued/paused -> 409; 8 concurrent POST /api/jobs -> eight 201s, unique ids, positions 1-8 matching queue order; 8 concurrent pause/move/delete ops keep positions dense; resume of a cancelled job and restart enqueue (queued, positions 1-2); 4 concurrent restarts of one job -> one 201 + three 409; GET /api/jobs status/limit filters and 422s; no _id anywhere. Settings: 30+ invalid PUT bodies (types, ranges, null, bool, NaN/Infinity, unknown/extra keys, non-object, bad JSON) -> 422 with nothing changed; boundaries 0/600 and 10/3600 accepted; partial PUT, trimming, persistence, GET /api/config mirrors settings; reset restores .env; new job without model/url uses the settings model (stub-503) and logs "step delay 0s, timeout 15s"; step delay 0 vs 3 s measured between steps; a settings change during a run does not affect it; resume and restart pick up the current settings. New suite /app/backend/tests/test_queue_settings.py. Also ran queue_smoke_test_prefix.sh (copy of Axiom's queue_smoke.sh; only change is the test_ title prefix) plus a regression of test_backend_api.py and test_job_controls.py (Axiom's updates kept, stub-503-once N=6).
Result: PASS
Passed: 49 tests + 58 smoke checks   Failed: 0
Failures: none
Retested: none (no Retest items or FIXED issues)
Settings: original values matched the .env defaults (http://localhost:9090, gpt-4o, 2 s, 300 s); restored with POST /api/settings/reset and checked with GET.
Not tested: backend restart with queued/paused jobs and with a running job ("interrupted by server restart") - the tester may not restart a running backend; request-timeout expiry itself (the stub cannot be slower than 5 s, so I only checked the timeout value in the job log); 500-entry log cap; real arena2api.

### Backend test run - 2026-10-01 21:45 ET
Tester: backend testing agent
Scope: feat/history-panel (PR #4), run through Caddy (TEST_BASE_URL=http://localhost:8080) with direct 127.0.0.1:8001 checks. New suite /app/backend/tests/test_history_extra.py:
- transcript JSON: shape, turns in step order matching the stored prompt/response/artifacts; pending steps omitted; queued/cancelled jobs have no turns; running and error turns are included.
- transcript.html: text/html; charset=utf-8, attachment roadmap2arena-<id8>-transcript.html, DOCTYPE through </html>, tags balanced (parsed), only safe tags/attributes, no script/iframe/img/link/a. Escaping checked with <script>, &, quotes, </style>, <img onerror>, <iframe> and javascript: in the title, step titles, project context and description; emoji kept.
- files: list shape, sorting, versions [1,3], zip_path/zip_skip_reason for ../evil.py and /abs/x.py, size equals the downloaded bytes; empty list for queued/running/error jobs.
- files/download: bytes and text/plain; latest vs ?step=N; Content-Disposition basename. 23 traversal or unknown paths (../, %2e%2e, double-encoded, absolute, backslashes, NUL, file://, .env paths, 5000-char path) and raw encoded query strings -> 404 JSON, never file-system content. Missing/empty path and step 0/100001/-1/abc/1.5/2^70 -> 422.
- clone-source: shape and values equal the source; cloned_from persists in detail, list and GET /api/queue; "Clone of job X" log line; clone of a clone works. Unknown/empty/long/non-string cloned_from -> 422 with nothing created; null -> no clone.
- unknown/malformed ids -> 404 on all new endpoints; POST/PUT/DELETE/PATCH -> 405; no _id anywhere.
Also ran Axiom's test_history.py 7/7 plus a regression of test_queue_settings.py 10/10, run_tests.py 21/21, test_job_controls.py 18/18 (ONCE_N=8; I did not restart arena-stub because restarting services is not allowed for the tester) and a direct-port sanity subset 6/6.
Result: PASS
Passed: 73   Failed: 0
Failures: none
Retested: none (no Retest items or FIXED issues)
Settings: original values equal the .env defaults; reset with POST /api/settings/reset at the end and checked with GET.
Notes: a %2F in the URL path (/files%2Fdownload) is decoded by the server and served by the normal route (only the job's own file), so this is not a bug. One test-file edit between runs triggered a uvicorn reload while no job was running.
Not tested: backend restart with queued/running jobs; request-timeout expiry; real arena2api.

### Frontend test run - 2026-10-01 21:54 ET
Tester: frontend testing agent
Scope: Full e2e of the tabbed dashboard (feat/history-panel, PR #4) at http://localhost:8080 in headless Chromium, desktop 1280x800 and mobile 390x844 (is_mobile + touch), real backend + :9090 stand-in. Every tab (Create, Job queue order/move up/down/pause/unpause/remove, Current job, Job history, Settings save/reset/persistence); Current job auto pick-up and reset to "Waiting for next job"; history panel collapse/expand + localStorage persistence; status badges; ?tab=history&job=<id> deep links (valid, invalid, reload; mobile back bar); job detail transcript, HTML export, per-file view/download, ZIP; Clone (prefill, edits, enqueue with cloned_from). Script: /app/frontend/tests/e2e_history.py (probes: mobile_probe.py, mobile_probe2.py, probe_badlink.py), state kept in /tmp. The Test Request says to put scripts in /app/backend/tests/, but the requester asked for /app/frontend/tests/. That is also safer, because the backend runs uvicorn --reload over that folder. No route interception needed. Settings were recorded first and restored afterwards (same four values, updated_at is new). 22 test jobs (test_xjqtf...) were removed from Mongo by id; no job delete API/UI exists (queue Remove only cancels).
Result: FAIL
Flows:
desktop tabs (incl. unknown route, ?tab=bogus) - PASS
desktop create_validation - PASS
desktop settings - PASS
desktop queue - PASS
desktop pickup - PASS
desktop stop_error - PASS
desktop badges - PASS
desktop panel_collapse - PASS
desktop deep_link - PASS
desktop detail - PASS
desktop clone - PASS
mobile clickables/overflow on top-level tabs - PASS
mobile tabs - PASS
mobile create_validation - PASS
mobile settings - PASS
mobile queue - PASS
mobile pickup - PASS
mobile stop_error - PASS
mobile badges - PASS
mobile panel_collapse - PASS
mobile deep_link (incl. All jobs back bar, browser back) - PASS
mobile detail - FAIL (transcript, export, file download, ZIP and Steps passed; Log tab click failed)
mobile clone - PASS
Failures:
- [F-003][MAJOR] mobile detail, click Log tab in job detail (mobile only) - expected all four detail tabs inside the 390px viewport and clickable vs actual document is 474px wide (tabs-list right edge 474px, Log tab 370-472px), page scrolls sideways and the touch click on Log timed out (pointer intercepted) twice - HistoryDetail.jsx TabsList has no overflow-x-auto wrapper (the main tab bar in App.jsx has one). Screenshots /tmp/frontend-test/mobile_detail.png, mobile_detail_tabs_mobile.png
- [F-004][MINOR] deep_link, unknown job id - expected only the not-found message vs actual extra toast "Lost contact with the backend ... retrying automatically" - useJob.js shows that toast for every error including 404. Screenshot /tmp/frontend-test/desktop_deep_link_invalid.png
- [F-005][MINOR] history list, queued job row - expected "#N in queue" vs actual never shown - api.js listJobs() mapping omits queue_position
Console errors: "Failed to load resource" 404 x15 (expected: invalid-id deep links), 422 x3 (expected: roadmap/parse with no steps). No page exceptions, no React warnings.
Network failures: None (no requestfailed, no CORS errors, no wrong host, no missing /api prefix; only external requests are Google Fonts). /api >=400 only the expected 404/422 above.
Retested: none (no Retest items, no FIXED issues; F-002 stays SKIPPED)
Not tested: Resume/Restart controls beyond their presence; stub-503-once-at-N and stub-slow-at-N; arena-stub restart (not allowed); real arena2api. The brief "Waiting for next job" state between two queued jobs (~1 s, 2 s queue poll) was not caught by the sampler; the reset was confirmed after the last job and after a stop, and the automatic hand-over A -> C -> B was seen on desktop and mobile.
Status list (status.jsx STATUS_META): pending, running, done (Completed), error (Failed), stopped, queued, paused, cancelled, idle; all 7 job statuses (Queued, Running, Paused, Completed, Failed, Stopped, Cancelled) were produced and their badges seen in the history list and filter chips.

### Backend test run - 2026-10-01 23:17 ET
Tester: backend testing agent
Scope: branch feat/notifications (PR #6 job deletion + PR #7 notifications). All destructive tests ran on a private backend from backend/tests/isolated_server.py (127.0.0.1, free port, throwaway DB roadmap2arena_test_<hex>, dropped on exit). I checked the harness first: DB_NAME is overridden by env (load_dotenv does not override), it binds 127.0.0.1, and it drops only its own DB. Webhooks went to a local http.server receiver (ok/500/slow 12 s/refused) and email to local aiosmtpd sinks (plain, AUTH-required, 554-reject, refused port), all on 127.0.0.1 inside the test process. No real email or outside host was contacted.
New suite /app/backend/tests/test_delete_notify_extra.py:
- Deletion: hard delete removes the job and its steps (Mongo-checked) and every sub-resource/action returns 404; running -> 409 and bulk skip "running"; queued/paused delete renumbers the queue; cancel vs the deprecated DELETE alias (same body; Deprecation/Link headers only on the alias); bulk with mixed/duplicate/blank/unknown/running ids, 500-id max and 422s; delete-finished (isolated DB only) removes only done/error/stopped/cancelled.
- Deletion races: 6 parallel deletes -> one 200; delete vs start at 7 offsets -> either 200 (queued, never ran) or 409 (started); parallel bulk + delete-finished + single deletes -> each id deleted exactly once, no orphan steps.
- Notifications: settings shape and 35 invalid bodies -> 422 with nothing saved; password write-only (omitted/mask keep, "" clears, unsaved test override not saved); test webhook/email to the local sinks incl. 500/refused/timeout/STARTTLS/554/auth errors; the 4 events with payload shape, per-run-once, queue_empty only when drained with the paused note; toggles off -> silent; in-app off + webhook on; slow/failing webhook and SMTP never delay or break jobs (next job started <2 s); list/read/read-all/delete/clear incl. concurrent calls; no _id.
Also ran Axiom's test_deletion.py 10/10 and test_notifications.py 12/12 (isolated), then a regression on the main backend: run_tests.py 21/21, test_job_controls.py 18/18 (ONCE_N=9), test_queue_settings.py 10/10, test_history.py 7/7, test_history_extra.py 11/11 (via :8080).
Result: FAIL
Passed: 105   Failed: 2
Failures:
- [B-004][MAJOR] PUT /api/notifications/settings with a JSON list/string body containing a password - expected 422 without the password vs actual 422 whose "input" echoes the submitted password (same for POST /api/notifications/test/email) - notification_routes.py put_settings/test_email take `body: dict = Body(...)`, so FastAPI's default validation error includes the raw input (reproduced in 2 runs and with curl)
- [B-005][MINOR] DELETE /api/notifications/settings - expected 405 vs actual 404 "Notification settings not found" - DELETE /api/notifications/{notif_id} route matches "settings" (reproduced twice)
Retested: none for the backend. F-003, F-004, F-005 and V-001..V-008 are frontend items; I can't retest them as backend tester, so their status is unchanged.
Observations (not tracked):
- Webhook and email deliveries for one event run one after the other, so a slow webhook delays the email result by up to 10 s; jobs are not affected.
- In one stop-vs-finish race (stop at about 5.0 s on a 1-step job) the job ended "stopped" with stopped_step null after its only step was done (resume then says nothing left). Seen once.
Cleanup: all test_ jobs deleted. The 112 notifications my main-backend regression created were deleted by Mongo query (created during my run window, titles test_ or queue_empty); main had no other notifications. Run settings equal the .env defaults (checked with GET). Main notification settings were never changed (webhook/email disabled, no password set). Nothing queued or running.
Processes I started: isolated uvicorn backends via the harness (one per suite run, each terminated, DB dropped) and the in-process local webhook/SMTP sinks (stopped at the end of each run). /tmp/reg_wt.py and its isolated uvicorn plus the DBs roadmap2arena_test_749ca62f and roadmap2arena_test_c93ff1a4 are not mine and were left alone.
Not tested: backend restart mid-job; real SMTP/webhook providers (not allowed); TLS/SSL SMTP against a real TLS server; the 500-notification cap.


### Backend test run - 2026-10-02 00:52 ET
Tester: backend testing agent
Scope: worktree /workspace/r2a-wt, branch feat/github-integration, Test Requests (C) PR #8 git integration and (D) PR #9 GitHub. Everything ran on isolated backends (backend/tests/isolated_server.py: 127.0.0.1, free port, throwaway DB, dropped on exit) with R2A_DATA_DIR in temp dirs, a private arena stub, and tests/github_stub.py on a free port with local bare repos. GITHUB_API_URL/GITHUB_OAUTH_URL pointed at the stub (checked in the server's /proc environ), R2A_GITHUB_TOKEN was empty, and a fresh random R2A_SECRET_KEY was generated per run and never written down. The server process had no non-loopback TCP connections, and the stub saw the expected API calls. No real GitHub was contacted.
New suite backend/tests/test_git_github_extra.py (21 tests). It covers:
- B-004/B-005 retest.
- PR #8: one repo per job, with one commit per step ("Step N: <title>", ROADMAP2ARENA author/committer, R2A-Job/R2A-Step trailers, commit_sha on steps). It also covers the unsafe stub paths kept inside the repo, and commit detail and compare checked against `git show/diff --numstat` (short sha, root commit, base==head, reversed, cross-job sha -> 404).
- 4xx checks: 4xx for unknown or malformed job ids, shas, refs (HEAD, `--output=`, `-R`, `..`), step indexes, limit and format. Zip and bundle downloads are valid (testzip, prefix, contents equal to HEAD, extracted repo fsck, bundle verify and clone). Delete removes the repo dir, the repos doc and the watches.
- PR #9 connection: PAT connect, replace and disconnect. The token is Fernet-encrypted (gAAAA..., no plaintext or base64 anywhere in the DB, decrypts with the server key, a wrong key raises InvalidToken; a token stored under another key -> token_error and 409, no 500).
- OAuth device flow: start (no device_code exposed), pending, slow_down, approve, deny, expire and cancel. PAT vs OAuth gives 409 both ways, including while a device code is pending.
- Push and PRs: push to a new repo lands 3 commits in the bare repo, and there is no token or extraheader in either .git/config. Name-exists and non-fast-forward -> 409, read-only -> 403, missing -> 404, 19 invalid bodies -> 422 with no stray dirs. PR creation via the stub works (existing PR returns existing:true, no duplicate notification), and a missing base gives pr_error.
- Stub error mapping: 401/403 (+permission hint)/404/422/451/429 (Retry-After)/403 rate limit -> 429/5xx x2 -> 502, the GET retries once, unreachable host -> 502, no key -> 409, never a 500.
- Watcher: checks_passed, pr_merged, outside push -> github_pushed, checks_failed, no duplicates, rate_low pause.
- Leaks: a response hook checked every response for tokens, the key and _id. Isolated logs, the stub log, job logs and notifications were grepped for every stub token (raw and base64): none found.
Regression (all isolated, TEST_BASE_URL/TEST_DIRECT_URL = isolated server): run_tests.py 21/21, test_job_controls.py 17/17 (ONCE_N=13), test_queue_settings.py 10/10, test_history.py 7/7, test_history_extra.py 11/11, test_deletion.py 10/10 (alone, no flake), test_notifications.py 12/12, test_delete_notify_extra.py del 8/8 + notif 10/10, test_git.py 15/15, test_github.py 32/32, test_github_client.py 9/9.
Result: FAIL
Passed: 182   Failed: 1
Failures:
- [B-006][MAJOR] POST /api/jobs/{id}/github/push {"branch": "@{-1}"} - expected 422 vs actual 502 "git push failed ... invalid refspec" - git_cli.valid_branch runs check-ref-format --branch with cwd=None, so @{-N} is expanded against the backend's cwd repo (reproduced in 3 runs)
Also filed: [B-007][MINOR] POST /api/github/oauth/poll returns 200 {status: connected} when already connected, where the contract says 409 (intentional in code; doc or code should change).
Retested: B-004 VERIFIED (list/string/nested bodies with a password and with a GitHub token -> 422 without "input" and without the secret), B-005 VERIFIED (405). F-003..F-005 and V-001..V-008 are frontend items and stay unchanged.
Backend log: no tracebacks in any isolated log except 4 expected logged "git sync failed" (NotADirectoryError) from test_git's deliberate git-failure test, where the job still finished. No token in any log.
Harness note: two older suites still reach the live stack with hardcoded defaults. test_history_extra.py uses TEST_DIRECT_URL (default :8001), and on the first run it sent read-only GETs for ids that only exist in the isolated DB, which got 404s. test_backend_api.py test_proxy_routing always does GET http://localhost:8080/api/config. Both were read-only with no data change. I reran test_history_extra with TEST_DIRECT_URL set to the isolated server.
Cleanup: all isolated servers stopped and their DBs dropped. The github_stub processes, the private arena stub, temp data dirs, bare repos and my /tmp logs were removed. Pre-existing processes (live :8001, shared :9090 stub, github_stub :45679, arena stub :19190) and DBs roadmap2arena_test_24c74d4c/54a94a66/749ca62f are not mine and were left alone. backend/data/repos (Axiom's) was not touched.
Not tested: push timeout -> 504 (300 s), the GitHub read timeout (20 s), auto-push on job completion, the 7-day watch expiry, diff truncation caps, real GitHub (not allowed).

### Frontend test run - 2026-10-02 01:27 ET
Tester: frontend testing agent
Scope: Live app at http://localhost:8080 on PR #9 code (detached dcd0a2c, PRs #6-#9), headless Chromium, desktop 1280x800 and mobile 390x844 (is_mobile + touch), arena stub :9090. Script /app/frontend/tests/e2e_pr9.py, state in /tmp/r2a4, screenshots in /tmp/frontend-test. The Test Request is labelled Agent: backend and asks for the /workspace/r2a-wt worktree with isolated servers; the requester asked for the live :8080 app with scripts in /app/frontend/tests, so I followed that. Covered: deletion (#6), alerts and notification settings (#7), Git tab (#8), octicons and icon-button names (#9), regression. Interception: every browser request to *github.com / *githubusercontent.com was set to be aborted, and GitHub token/OAuth/push API calls were stubbed in the browser, because the live backend has no GITHUB_API_URL and would call real GitHub. Webhook/SMTP tests used only http://127.0.0.1:9/hook and 127.0.0.1:2525. The run was interrupted once and resumed. The GitHub Settings and Push dialog flows ran later, after Ian approved them. They used one extra test_ job (2 steps, deleted through the app afterwards). Only the malformed "short" token reached the backend (local 422); the well-formed fake-token connect, OAuth start/poll/cancel, the client-id save, disconnect and every push were stubbed or aborted in the browser. 0 requests to github.com or api.github.com were attempted. step_delay_seconds was set to 12 for part of the run so running states could be seen, then restored.
Result: FAIL
Flows (desktop and mobile unless noted):
r_f004 bad job link, no backend-lost toast - PASS
r_f005 "#N in queue" + delete guards (running delete disabled with hint, API 409, not selectable in bulk mode, queue Cancel, delete queued job) - PASS (needed 2-step jobs, see F-006)
r_layout (mobile detail 5-tab bar, main tabs, filters, header, single ZIP button, font sizes) - PASS
toasts done + queue empty + bell count - PASS
toasts stopped + failed (stub-503-at-3: "Job failed ... at step 3 - arena2api returned 503 ...") - PASS
bell: unread count, mark read, mark all, remove, clear (applied to this run's alerts only) - PASS
notification settings: per-event toggle save/reload, webhook test and email test give clean errors ("Failed: ConnectError ...", "Failed: ConnectionRefusedError ..."), SMTP password type=password, masked placeholder after save, never in PUT/GET responses or the DOM, cleared again - PASS
git tab: 2 commits for 2 steps, DiffViewer split/unified, collapse/expand, file list with +/- counts, compare (default and same sha), repo zip (~21.7 KB with .git) and bundle - PASS
a11y icons: octicons render (11 on Git tab), no icon-only button without an accessible name - PASS
regression: tabs, settings save/validation/reset, queue order/move/pause/unpause/cancel + pickup and "Waiting for next job", history panel persistence + deep link + downloads, clone - PASS
delete: single, bulk (2), delete-all-finished (applied to own finished jobs) - PASS
github settings (01:30-01:35 ET, approved by Ian; fake token ghp_test_fake000... only): token input type=password, Connect disabled when empty, malformed token -> backend 422 "does not look like a GitHub token" (no GitHub call), well-formed fake token -> stubbed 401 shown in gh-error, token not in the DOM outside its own input, backend stays not connected; stubbed PAT-connected state hides the token form and disables OAuth (gh-oauth-disabled-note), auto-push private toggle follows enabled, two-click disconnect; stubbed OAuth pending (fake client id) disables the PAT input/Connect, cancel re-enables them; real GitHub state unchanged - PASS
push to GitHub dialog (form/validation only): real state shows "not connected" + Open settings; stubbed connected: default repo name and r2a/job-<id8> branch, empty repo name or blank branch disables Push, branch "@{-1}" -> 422 error shown (stubbed), existing-repo mode lists repos with read-only one disabled, Open PR fields and "Push and open PR" label; a valid push request was aborted in the browser and the dialog shows a clean "Cannot reach the backend" error - PASS
job with 4 stub-slow steps (first r_f005 attempt and rerun) - FAIL (F-006)
Failures:
- [F-006][CRITICAL] 4-step stub-slow job - expected Completed vs actual "Job interrupted by server restart" at step 3, twice. Backend log: "StatReload detected changes in 'data/repos/<job>/app/main.py'. Reloading...". Screenshot /tmp/frontend-test/desktop_job_run_backend_reload.png
Console errors: only "Failed to load resource" 404s from the deliberate bad-id deep links, plus expected ones from the GitHub flows: 422 (malformed token, stubbed bad branch), 401 (stubbed fake token) and ERR_FAILED (push aborted on purpose). No page exceptions.
Network failures: none. No requestfailed, CORS, wrong host or missing /api prefix; /api >=400 only the expected 404s and the GitHub-flow 401/422 above; the one requestfailed is the push I aborted on purpose. No request to github.com or api.github.com was attempted (0 aborted). 4 push requests (2 desktop, 2 mobile) were stubbed or aborted in the browser, and none reached the backend.
Retested: F-003 VERIFIED (mobile), F-004 VERIFIED, F-005 VERIFIED, V-001/V-002/V-004/V-005/V-006/V-007 VERIFIED (layout checks on mobile/desktop), B-006 VERIFIED at function level (git_cli.valid_branch rejects "@{-1}", "@", "main@{u}", "HEAD", "a//b" and accepts "feature/x", "r2a/job-1", "main", "release-1.2"; the push route can't be reached without a GitHub connection). B-007 left FIXED: only checked that oauth/poll with nothing pending and not connected gives 409; the connected case needs a real connection. V-003/V-008 are visual and left FIXED. F-002 stays SKIPPED.
Not reproduced on rerun (not tracked): one mobile queue run where the unpaused job stayed paused on the server; rerun passed.
Cleanup: all 33 test_ jobs of this run (32 + 1 for the GitHub flows) deleted through the app (DELETE /api/jobs/{id} and bulk-delete); their repos are gone. This run's 12 leftover alerts deleted by id; the 18 earlier alerts are untouched (still unread). Settings, notification settings (webhook/SMTP off and empty, password_set false) and GitHub state (not connected, no client id) match the values recorded before the run; only updated_at changed.
Not tested: Browser notification toggle (headless Chromium reports permission "denied", so the toggle is disabled). Real GitHub, webhook and SMTP (not allowed).
