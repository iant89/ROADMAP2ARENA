# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Agent: backend
Date: 2026-10-01 19:59 ET
Built or changed: FastAPI backend for ROADMAP2ARENA (jobs, steps, orchestrator, arena2api client, artifact extractor, ZIP), MongoDB persistence
Key endpoints or flows: GET /api/config, POST /api/roadmap/parse, POST /api/jobs, GET /api/jobs, GET /api/jobs/{id}, GET /api/jobs/{id}/steps/{index}, GET /api/jobs/{id}/download
Data: real backend
Features present: sessions no (but each job has its own conversation and artifacts; jobs must never mix), auth no, integrations: arena2api via local stand-in only
Retest: none
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

## Test Log

