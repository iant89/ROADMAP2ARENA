# ROADMAP2ARENA - Test Results

## Testing Protocol

- The main agent builds features and fixes bugs. Testing agents only test and report; they never change application code.
- Testing agents may edit only two sections of this file: append new entries to **Test Log** (append-only, never rewrite or delete earlier entries) and add or update rows in **Issue Tracker**.
- Each Test Log entry: date/time, tester, scope, what was tested, result (PASS/FAIL), and linked issue IDs.
- Severity levels: BLOCKER, MAJOR, MINOR, NIT.
- The main agent reads this file before each fix cycle, fixes open issues, and updates the Test Request section.

## Test Request

Backend integration (branch feat/backend-integration). Use the LOCAL arena2api stand-in
(backend/tests/arena_stub.py on 127.0.0.1:9090, supervisor program arena-stub), not the real gateway.
Please verify:
- GET /api/config, POST /api/roadmap/parse (steps and 422 for no steps).
- POST /api/jobs: 201, 409 while a job runs (model stub-slow), 422 for bad arena_url / empty model / empty or stepless roadmap.
- Job runs to done; GET /api/jobs/{id} progress, steps[].artifact_paths, log; GET /steps/{index} prompt/response/artifacts; 404s.
- 503 handling with model stub-503 or stub-503-at-N (error message + Chrome tab hint, later steps pending).
- GET /download: latest version per path, ../evil.py skipped and logged, /abs/x.py stored as abs/x.py, 409 with no artifacts.
- Backend restart while running marks the job "interrupted by server restart".
- UI at http://localhost:8080: defaults from /api/config, live parse preview, running/done/error states, transcript on demand, ZIP download, Recent jobs, /?job=<id> deep link.

## Issue Tracker

| ID | Severity | Area | Summary | Status |
|----|----------|------|---------|--------|

## Test Log

