# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Agent: frontend
Date: 2026-10-01 20:11 ET
Built or changed: Inline validation message for empty model (F-001)
Key endpoints or flows: validation flow (clear the Model field or enter whitespace -> inline "Model is required" under the field and Start job disabled; a non-http(s) arena2api URL such as ftp://... -> inline "Use an http:// or https:// URL"; fixing the values removes the messages and re-enables Start) + quick regression of run_job, artifacts, download_zip, error_job
Data: real backend
Features present: sessions no, auth no, integrations: arena2api via local stand-in at http://localhost:9090 only (form default URL)
Retest: F-001
Test accounts: none
Notes: Same environment as before: UI at http://localhost:8080 (Caddy -> Vite 5173 and /api -> backend 127.0.0.1:8001), local arena2api stand-in on 127.0.0.1:9090 (magic models stub-503, stub-503-at-3, stub-slow). Only one job can run at a time (409 otherwise), so wait for each job to finish. Inline messages appear only after a field has been edited (not on first load). F-002 is intentionally not fixed in this round. Do not edit application code.

## Issue Tracker

| ID | Severity | Area | Summary | Status |
|----|----------|------|---------|--------|
| B-001 | CRITICAL | GET /api/jobs/{id}/steps/{index} | index > 2^63-1 (e.g. 99999999999999999999999) returns 500; OverflowError from pymongo in server.py get_step | VERIFIED |
| B-002 | MAJOR | POST /api/jobs | One-job-at-a-time rule is racy: 5 concurrent POSTs all returned 201 and 5 jobs ran at once (expected one 201 + four 409); check-then-insert in server.py create_job | VERIFIED |
| F-001 | MAJOR | Frontend StartForm validation | Empty/whitespace model only disables Start job silently; no error message explains why (expected a clear error) | VERIFIED |
| F-002 | MINOR | Frontend App URL state | Starting/opening a job does not put ?job=<id> in the URL, so a reload drops back to the empty form (job still reachable via Recent jobs) | SKIPPED |

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
