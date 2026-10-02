# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Agent: backend
Date: 2026-10-01 20:03 ET
Built or changed: FastAPI backend for ROADMAP2ARENA (jobs, steps, orchestrator, arena2api client, artifact extractor, ZIP), MongoDB persistence
Key endpoints or flows: GET /api/config, POST /api/roadmap/parse, POST /api/jobs, GET /api/jobs, GET /api/jobs/{id}, GET /api/jobs/{id}/steps/{index}, GET /api/jobs/{id}/download
Data: real backend
Features present: sessions no (but each job has its own conversation and artifacts; jobs must never mix), auth no, integrations: arena2api via local stand-in only
Retest: B-001, B-002
Test accounts: none
Notes:
- arena2api is replaced by a LOCAL stand-in (backend/tests/arena_stub.py, supervisor program arena-stub) on http://127.0.0.1:9090. Magic models: stub-503 (HTTP 503 on every call), stub-503-at-3 (503 on the 3rd user turn only; any stub-503-at-N works), stub-slow (sleeps 5 s per call, for 409 testing). Any other model gets turn-based canned replies containing a lang:path block, a "# filename:" block, an unnamed block, "../evil.py" and "/abs/x.py".
- Backend: http://127.0.0.1:8001/api directly, or via Caddy at http://localhost:8080/api (UI at http://localhost:8080). Step delay is 2 s (ARENA_STEP_DELAY_SECONDS).
- One job at a time: POST /api/jobs returns 409 while any job is running, so tests must poll GET /api/jobs/{id} until status is done or error before starting the next job.
- Edge cases to cover: 422 for arena_url not http/https, empty model, empty roadmap_md, roadmap with no steps (also POST /api/roadmap/parse); 409 second job; 404 unknown job and unknown step index; 409 download when a job has no artifacts; unnamed blocks never appear in artifact_paths or the ZIP; "../" paths skipped from the ZIP (and logged), absolute "/x" paths stored without the leading slash; later steps replace earlier versions of the same path in the ZIP; no "_id" in any response; ids are UUID4; timestamps are ISO 8601 UTC ending in Z; 503 error message includes the "check that the arena2api Chrome tab is open" hint and later steps stay pending; a backend restart mid-job marks it "interrupted by server restart".
- Put test scripts in /app/backend/tests/.

## Issue Tracker

| ID | Severity | Area | Summary | Status |
|----|----------|------|---------|--------|
| B-001 | CRITICAL | GET /api/jobs/{id}/steps/{index} | index > 2^63-1 (e.g. 99999999999999999999999) returns 500; OverflowError from pymongo in server.py get_step | VERIFIED |
| B-002 | MAJOR | POST /api/jobs | One-job-at-a-time rule is racy: 5 concurrent POSTs all returned 201 and 5 jobs ran at once (expected one 201 + four 409); check-then-insert in server.py create_job | VERIFIED |

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

