# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Agent: backend
Date: 2026-10-01 22:10 ET
Built or changed: job history and job detail (branch feat/history-panel, stacked on feat/tabs-and-queue). New read-only history endpoints (full transcript, standalone HTML export, file list, per-file download, clone source) and a new optional `cloned_from` on POST /api/jobs, stored on the job and returned in job detail, GET /api/jobs and GET /api/queue. Frontend: Current job tab shows "Waiting for next job" when nothing runs and picks up the next job automatically; Job history tab is a collapsible side panel (collapses to a rail, remembered in localStorage r2a.historyPanel.collapsed; on mobile the list gives way to an "All jobs" back bar when a job is open) with badges for every status (Queued, Running, Paused, Completed, Failed, Stopped, Cancelled), name and date; the job detail has Transcript / Files / Steps / Log tabs, Export transcript, per-file Download, Download ZIP and Clone job (prefilled editable sheet, submits a new queued/running job with cloned_from). URL state ?tab=history&job=<id>. Status labels changed: done -> "Completed", error -> "Failed" (API values unchanged).
Key endpoints or flows: GET /api/jobs/{id}/transcript -> {job_id, title, model, arena_url, status, created_at, finished_at, project_context, step_total, steps_done, turns[{step_index, step_title, status, prompt, response, error, artifact_paths, started_at, finished_at}]} (pending steps omitted); GET /api/jobs/{id}/transcript.html -> attachment roadmap2arena-<id8>-transcript.html, text/html, inline CSS, no scripts/links/src, all text escaped; GET /api/jobs/{id}/files -> [{path, step_index, versions, size, zip_path, zip_skip_reason}]; GET /api/jobs/{id}/files/download?path=&step= -> text/plain attachment (latest version without step; 404 unknown job/file/version; 422 missing path or step outside 1..100000); GET /api/jobs/{id}/clone-source -> {source_job_id, title, arena_url, model, project_context, roadmap_md, step_total}; POST /api/jobs with cloned_from (422 "cloned_from: job X not found" for unknown ids; job log "Clone of job X"); regression of all earlier endpoints (queue, settings, controls)
Data: real backend
Features present: sessions no, auth no, integrations: arena2api via local stand-in only
Retest: none
Test accounts: none
Notes:
- Stand-in on http://127.0.0.1:9090 (backend http://127.0.0.1:8001/api, or via Caddy http://localhost:8080/api). Stub unchanged. Magic models: stub-503, stub-503-at-N, stub-503-once-at-N (per stub process - use a fresh N), stub-slow (5 s every call), stub-slow-at-N (5 s only on turn N). gpt-4o replies instantly.
- Timing: a job takes about steps x reply time + (steps - 1) x step delay (2 s default). The next queued job starts ~1 s after the running one ends.
- History endpoints are read-only and work for every status (queued/paused jobs have no turns and no files yet). Files and transcript reflect the step versions stored on the job (a file replaced in a later step has versions > 1; ?step= picks a version). Paths the ZIP skips (e.g. "../evil.py") are listed with zip_skip_reason and are still downloadable individually as text.
- Clone takes the edited form values, not the source values; cloned_from only links back. A clone of a clone is allowed.
- Tests changed by the main agent (minimal, commented): key-set assertions now include cloned_from in tests/test_backend_api.py (2 places) and tests/test_queue_settings.py (1 place). New: tests/test_history.py (7 checks, cleans up its jobs). Results before handoff: run_tests.py 21/21, test_job_controls.py 17/17, test_queue_settings.py 10/10, test_history.py 7/7; UI e2e (Playwright) 30/30. Restart arena-stub before a full run (stub-503-once-at-N fires once per stub process).
- Put test scripts in /app/backend/tests/. Please reset settings afterwards (POST /api/settings/reset).

## Issue Tracker

| ID | Severity | Area | Summary | Status |
|----|----------|------|---------|--------|
| B-001 | CRITICAL | GET /api/jobs/{id}/steps/{index} | index > 2^63-1 (e.g. 99999999999999999999999) returns 500; OverflowError from pymongo in server.py get_step | VERIFIED |
| B-002 | MAJOR | POST /api/jobs | One-job-at-a-time rule is racy: 5 concurrent POSTs all returned 201 and 5 jobs ran at once (expected one 201 + four 409); check-then-insert in server.py create_job | VERIFIED |
| F-001 | MAJOR | Frontend StartForm validation | Empty/whitespace model only disables Start job silently; no error message explains why (expected a clear error) | VERIFIED |
| F-002 | MINOR | Frontend App URL state | Starting/opening a job does not put ?job=<id> in the URL, so a reload drops back to the empty form (job still reachable via Recent jobs) | SKIPPED |
| B-003 | CRITICAL | POST /api/jobs/{id}/stop | Concurrent stop calls on a running job (4 parallel) all return 200 with status "running"; job and step stay "running" with no task (no stop log line), blocking every new job until stop is called again. Repeat cancel() in orchestrator.stop interrupts the CancelledError handler/mark_stopped in run_job | VERIFIED |
| F-003 | MAJOR | Frontend HistoryDetail (mobile 390px) | Job detail tab bar (Transcript/Files/Steps/Log) is not wrapped in a horizontal scroller, so the history detail page is ~474px wide at 390px: page scrolls sideways, Log tab starts off-screen and the touch-emulated click on it failed (intercepted) | OPEN |
| F-004 | MINOR | Frontend useJob / history deep link | ?tab=history&job=<unknown id> shows a "Lost contact with the backend ... retrying automatically" toast for a plain 404 (nothing is retried; the inline "Could not load job ... not found" message is correct) | OPEN |
| F-005 | MINOR | Frontend api.listJobs / HistoryPanel | listJobs() drops queue_position from GET /api/jobs, so the history list never shows the "#N in queue" hint for queued jobs | OPEN |

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
