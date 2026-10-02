# ROADMAP2ARENA - API contracts and integration plan

All routes are under `/api` on the FastAPI backend (127.0.0.1:8001; Caddy proxies
`/api/*` from :8080). Ids are UUID4 strings, timestamps are ISO 8601 UTC
(`2026-10-01T23:52:35.726Z`). Mongo `_id` is never returned. Errors use
FastAPI's `{"detail": "..."}` shape.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/api/config` | `{arena_url, model, step_delay_seconds, request_timeout_seconds}` = current settings (form defaults) | - |
| GET | `/api/settings` | `{arena_url, model, step_delay_seconds, request_timeout_seconds, updated_at, env_defaults:{...same 4}}` | - |
| PUT | `/api/settings` body: any subset of the 4 fields | same shape as GET (saved) | 422 unknown key, arena_url not http(s), empty model, delay not 0-600, timeout not 10-3600, non-number/bool, bad JSON |
| POST | `/api/settings/reset` | same shape as GET, values = backend/.env | - |
| POST | `/api/roadmap/parse` body `{roadmap_md}` | `{steps:[{index,title,description}], title}` | 422 no steps / bad body |
| POST | `/api/jobs` body `{arena_url?, model?, project_context?, roadmap_md}` (defaults from settings) | 201 `{job_id, status:"running"\|"queued", queue_position}` (never 409 - busy means queued) | 422 arena_url not http(s), empty model, empty roadmap or no steps |
| GET | `/api/jobs?status=a,b&limit=n` | most recent first, default 20, `limit` 1-200: `[{job_id,status,created_at,step_total,steps_done,title,model,failed_step,stopped_step,restarted_from,queue_position,queued_at,started_at,finished_at,paused}]` | 422 unknown status / limit out of range |
| GET | `/api/jobs/{id}` | `{job_id,status,error,failed_step,stopped_step,restarted_from,queue_position,queued_at,started_at,title,created_at,finished_at,arena_url,model,project_context,roadmap_md,step_total,steps_done,steps:[{index,title,description,status,error,artifact_paths}],log:[{ts,level,msg}]}` | 404 |
| GET | `/api/jobs/{id}/steps/{index}` | full step: `{job_id,index,title,description,status,prompt,response,error,artifacts:[{path,content}],started_at,finished_at}` | 404 job or step |
| POST | `/api/jobs/{id}/stop` | `{job_id,status:"stopped",stopped_step,steps_done}` | 404; 409 job not running (queued/paused: remove it from the queue instead) or finished before the stop took effect |
| POST | `/api/jobs/{id}/restart` body `{arena_url?, model?}` (optional) | 201 `{job_id, status, queue_position}` of a NEW job (enqueued) | 404; 409 job not finished, or a restart of this job is already queued/paused/running; 422 bad override |
| POST | `/api/jobs/{id}/resume` body `{arena_url?, model?}` (optional) | `{job_id, status:"running"\|"queued", queue_position, resumed_from_step}` (same job, enqueued) | 404; 409 job done/running/queued/paused or nothing left; 422 bad override |
| GET | `/api/queue` | `{running: summary\|null, queued:[summary in queue order], count, waiting}`; summary = `{job_id,status,title,model,step_total,steps_done,queue_position,queued_at,created_at,started_at,restarted_from,paused}` | - |
| POST | `/api/queue/{id}/move` body `{direction:"up"\|"down"}` or `{position:n}` | `{job_id,status,queue_position}` | 404; 409 not queued/paused; 422 neither/both fields, bad direction, position < 1 (large positions clamp to the end) |
| POST | `/api/queue/{id}/pause` | `{job_id,status:"paused",queue_position}` - moved to the END of the queue (idempotent) | 404; 409 not queued/paused |
| POST | `/api/queue/{id}/unpause` | `{job_id,status,queue_position}` - eligible again, keeps its position; may start at once | 404; 409 not queued/paused |
| DELETE | `/api/queue/{id}` | `{job_id,status:"cancelled",queue_position:null}` - kept in history | 404; 409 not queued/paused |
| GET | `/api/jobs/{id}/download` | `application/zip`, latest version per path | 404 unknown job; 409 no artifacts |

Statuses: job `queued|paused|running|done|error|stopped|cancelled`; step `pending|running|done|error|stopped`.
Finished (history) = `done|error|stopped|cancelled`; resumable = `error|stopped|cancelled`.

### Job queue (backend/scheduler.py)

- Every new job, restart and resume is **enqueued**: status `queued`, `queue_position`
  = end of the queue, `queued_at` = now. If nothing is running it starts in the same
  request (`status:"running"`, `queue_position:null`, `started_at` set).
- Queue positions are dense `1..n` over `queued` + `paused` jobs. Every queue change
  (enqueue, move, pause, unpause, remove, start) runs under one asyncio lock
  (`scheduler.lock`), which also guards "is anything running" + start, so exactly one
  job runs and positions stay consistent under concurrent requests.
- A worker task starts the first `queued` (not paused) job in position order whenever
  nothing is running. It is woken on job end (task done / stop recorded), enqueue,
  unpause and startup, and re-checks every 5 s as a safety net.
- Pause: `queued -> paused` and moved to the end. Unpause: `paused -> queued`, keeps its
  (end) position. Remove: `cancelled`, `finished_at` set, kept in history (resumable).
- Startup: `running` jobs become error "interrupted by server restart" (as before);
  queued/paused jobs keep their order and the worker starts the next one.
- Log lines: "Added to the queue", "Waiting in the queue at position n", "Started from
  the queue", "Paused - moved to the end of the queue (position n)", "Unpaused - eligible
  to run again", "Removed from the queue (cancelled)".

### Runtime settings (backend/app_settings.py)

- MongoDB collection `settings`, one document `_id:"app"` with `arena_url, model,
  step_delay_seconds, request_timeout_seconds, updated_at`. Seeded from backend/.env on
  first startup (`$setOnInsert`, values clamped into range); reset restores the .env values.
- `POST /api/jobs` defaults arena_url/model from settings. Each run (new, resumed,
  restarted) reads step delay and request timeout from settings when it starts and logs
  them ("..., step delay 2s, timeout 300s"); a running job keeps its values.
- No new environment variables.

### Job controls

- **Stop** cancels the job's asyncio task (registry by job_id). The in-flight arena
  request is abandoned; the running step becomes `stopped` (response discarded),
  later steps stay pending, job `stopped` with `finished_at` and a log line. A step
  is only marked done while still `running`, so nothing flips to done afterwards.
- **Restart** copies arena_url, model, project_context and roadmap_md (with optional
  overrides) into a new job (`restarted_from` = old id) that runs from step 1.
- **Resume** continues the same job from the first non-done step: that step and later
  ones are reset to pending (prompt/response/artifacts/error cleared), job error
  cleared, log "Resumed from step k". The orchestrator rebuilds the chat history from
  the done steps' stored prompt/response pairs (user, assistant, in order) and the
  file list from their artifacts, so the resumed request carries the full history.
- Restart and resume go through the queue (see below) and share its lock.
- Startup recovery still turns running jobs into error "interrupted by server
  restart"; such jobs are resumable.
Log levels: `info|ok|warn|error`; the job keeps the last 500 entries.

ZIP rules: backslash -> `/`, strip leading `/` and `./`, skip paths with a `..`
segment or an empty file name (each skip is written to the job log), unnamed
blocks are never stored as artifacts.

## What was mocked (removed in feat/backend-integration)

- `frontend/src/mock.js`: default config, sample jobs, canned responses -> now
  `GET /api/config` and real jobs in MongoDB. Sample roadmap text kept in
  `frontend/src/constants/sampleRoadmap.js` for the "Load sample roadmap" button.
- `frontend/src/lib/mockEngine.js` (in-browser job runner + localStorage) ->
  `backend/orchestrator.py` + MongoDB.
- `roadmapParser.js`, `artifacts.js`, `prompts.js`, `zip.js` (browser) ->
  `roadmap_parser.py`, `artifact_extractor.py`, `arena_client.build_prompt`,
  `GET /download`.
- "Mock mode" banner -> removed; a backend error banner with Retry replaces it.

## Backend implementation

- `settings.py`: every setting from `backend/.env` (MONGO_URL, DB_NAME,
  ARENA2API_URL, ARENA2API_MODEL, ARENA_STEP_DELAY_SECONDS,
  ARENA_REQUEST_TIMEOUT_SECONDS, CORS_ORIGINS); missing values fail at startup.
- `server.py`: routes, validation, startup indexes (`jobs.id` unique,
  `steps(job_id,index)` unique), marks leftover running jobs/steps as error
  "interrupted by server restart".
- `orchestrator.py`: one asyncio task per job, writes every state change to
  Mongo (motor), fail-fast, `ARENA_STEP_DELAY_SECONDS` pause between steps,
  503 errors carry the hint "check that the arena2api Chrome tab is open and
  pushing tokens".
- `arena_client.py`: AsyncOpenAI(base_url=`{arena_url}/v1`, api_key
  `sk-anything`, timeout from env, max_retries 0), temperature 0.2, no streaming,
  rolling chat history per job; prompt formats match the former mock builder.
- `artifact_extractor.py`: `BLOCK_RE` for fenced blocks, `lang:path` info string
  or `PATH_COMMENT_RE` first-line comment; `clean_zip_path` for the ZIP.
- Collections: `jobs` (job doc + log array), `steps` (one doc per step with
  prompt, response, artifacts list).

## Frontend integration

- `src/lib/api.js` is the only data layer: fetch to
  `${VITE_BACKEND_URL}/api/...`, GET retries (2, exponential backoff) on network
  errors/5xx, readable `ApiError` messages.
- `useJob` polls `GET /api/jobs/{id}` every 1.5 s while running/queued/paused;
  transient errors keep the last state and retry. `useQueue` polls `GET /api/queue`
  every 2 s (header status + progress, queue badge, Current job).
- Top-level tabs: Create job | Job queue | Current job | Job history | Settings.
  URL keeps place: `?tab=create|queue|current|history|settings&job=<id>` (job only for
  history); a bare `/?job=<id>` opens that job in Job history. The Recent jobs sheet
  was replaced by Job history (`GET /api/jobs?status=done,error,stopped,cancelled&limit=200`,
  client-side search + status filter).
- Artifact contents and transcript prompt/response are fetched on demand via
  `GET /steps/{index}` (finished steps cached).
- ZIP via `GET /download`; filename from Content-Disposition.
- `/?tab=history&job=<id>` deep-links to a job.

## Testing

- `backend/tests/arena_stub.py` is a LOCAL stand-in for arena2api on
  127.0.0.1:9090 (supervisor program `arena-stub`). Magic models: `stub-503`,
  `stub-503-at-N`, `stub-slow`. Turn-dependent canned replies include a
  `lang:path` block, a `# filename:` block, an unnamed block, `../evil.py` and
  `/abs/x.py`. More magic models: `stub-503-once-at-N` (503 on turn N only the
  first time per stub process), `stub-slow-at-N` (slow only on turn N). Every reply
  starts with "(stub reply for user turn N of the conversation)", which makes the
  resume history rebuild observable.
- Queue checks: `backend/tests/queue_smoke.sh` (curl + jq; enqueue/reorder/pause/
  unpause/remove/scheduler order/settings validation; uses `stub-slow`, ~40 s).
  `stub-slow` (5 s per reply) keeps a job running long enough to queue others behind it.
