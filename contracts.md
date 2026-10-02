# ROADMAP2ARENA - API contracts and integration plan

All routes are under `/api` on the FastAPI backend (127.0.0.1:8001; Caddy proxies
`/api/*` from :8080). Ids are UUID4 strings, timestamps are ISO 8601 UTC
(`2026-10-01T23:52:35.726Z`). Mongo `_id` is never returned. Errors use
FastAPI's `{"detail": "..."}` shape.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/api/config` | `{arena_url, model, step_delay_seconds}` from env | - |
| POST | `/api/roadmap/parse` body `{roadmap_md}` | `{steps:[{index,title,description}], title}` | 422 no steps / bad body |
| POST | `/api/jobs` body `{arena_url?, model?, project_context?, roadmap_md}` | 201 `{job_id}` | 409 a job is running; 422 arena_url not http(s), empty model, empty roadmap or no steps |
| GET | `/api/jobs` | 20 most recent: `[{job_id,status,created_at,step_total,steps_done,title,model,failed_step}]` | - |
| GET | `/api/jobs/{id}` | `{job_id,status,error,failed_step,title,created_at,finished_at,arena_url,model,project_context,roadmap_md,step_total,steps_done,steps:[{index,title,description,status,error,artifact_paths}],log:[{ts,level,msg}]}` | 404 |
| GET | `/api/jobs/{id}/steps/{index}` | full step: `{job_id,index,title,description,status,prompt,response,error,artifacts:[{path,content}],started_at,finished_at}` | 404 job or step |
| GET | `/api/jobs/{id}/download` | `application/zip`, latest version per path | 404 unknown job; 409 no artifacts |

Statuses: job `running|done|error`; step `pending|running|done|error`.
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
- `useJob` polls `GET /api/jobs/{id}` every 1.5 s while running; transient errors
  keep the last state and retry.
- Artifact contents and transcript prompt/response are fetched on demand via
  `GET /steps/{index}` (finished steps cached).
- ZIP via `GET /download`; filename from Content-Disposition.
- `/?job=<id>` deep-links to a job.

## Testing

- `backend/tests/arena_stub.py` is a LOCAL stand-in for arena2api on
  127.0.0.1:9090 (supervisor program `arena-stub`). Magic models: `stub-503`,
  `stub-503-at-N`, `stub-slow`. Turn-dependent canned replies include a
  `lang:path` block, a `# filename:` block, an unnamed block, `../evil.py` and
  `/abs/x.py`.
