# ROADMAP2ARENA - API contracts and integration plan

Application routes are under `/api` on the FastAPI job backend (127.0.0.1:8001;
Caddy proxies `/api/*` from :8080). The separately launched arena2api gateway
uses its own `/v1` and health/extension routes on loopback :9090 (see below). Ids are UUID4 strings, timestamps are ISO 8601 UTC
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
| POST | `/api/jobs` body `{provider_id?, arena_url?, model?, project_context?, roadmap_md, cloned_from?}` (provider resolution in "Providers"; model defaults to the provider's default_model, then settings; `cloned_from` = source job id from "Clone job", 422 if unknown) | 201 `{job_id, status:"running"\|"queued", queue_position}` (never 409 - busy means queued) | 422 unknown provider_id, arena_url not http(s), empty model, empty roadmap or no steps |
| GET | `/api/jobs?status=a,b&limit=n` | most recent first, default 20, `limit` 1-200: `[{job_id,status,created_at,step_total,steps_done,title,model,failed_step,stopped_step,restarted_from,queue_position,queued_at,started_at,finished_at,paused}]` | 422 unknown status / limit out of range |
| GET | `/api/jobs/{id}` | `{job_id,status,error,failed_step,stopped_step,restarted_from,queue_position,queued_at,started_at,title,created_at,finished_at,arena_url,model,provider,project_context,roadmap_md,step_total,steps_done,steps:[{index,title,description,status,error,artifact_paths}],log:[{ts,level,msg}]}` | 404 |
| GET | `/api/jobs/{id}/steps/{index}` | full step: `{job_id,index,title,description,status,prompt,response,error,artifacts:[{path,content}],started_at,finished_at}` | 404 job or step |
| POST | `/api/jobs/{id}/stop` | `{job_id,status:"stopped",stopped_step,steps_done}` | 404; 409 job not running (queued/paused: cancel it via the queue instead) or finished before the stop took effect |
| POST | `/api/jobs/{id}/restart` body `{provider_id?, arena_url?, model?}` (optional) | 201 `{job_id, status, queue_position}` of a NEW job (enqueued) | 404; 409 job not finished, or a restart of this job is already queued/paused/running; 422 bad override |
| POST | `/api/jobs/{id}/resume` body `{provider_id?, arena_url?, model?}` (optional) | `{job_id, status:"running"\|"queued", queue_position, resumed_from_step}` (same job, enqueued) | 404; 409 job done/running/queued/paused or nothing left; 422 bad override |
| GET | `/api/queue` | `{running: summary\|null, queued:[summary in queue order], count, waiting}`; summary = `{job_id,status,title,model,step_total,steps_done,queue_position,queued_at,created_at,started_at,restarted_from,paused}` | - |
| POST | `/api/queue/{id}/move` body `{direction:"up"\|"down"}` or `{position:n}` | `{job_id,status,queue_position}` | 404; 409 not queued/paused; 422 neither/both fields, bad direction, position < 1 (large positions clamp to the end) |
| POST | `/api/queue/{id}/pause` | `{job_id,status:"paused",queue_position}` - moved to the END of the queue (idempotent) | 404; 409 not queued/paused |
| POST | `/api/queue/{id}/unpause` | `{job_id,status,queue_position}` - eligible again, keeps its position; may start at once | 404; 409 not queued/paused |
| POST | `/api/queue/{id}/cancel` | `{job_id,status:"cancelled",queue_position:null}` - CANCEL: out of the queue, KEPT in history (resumable/restartable) | 404; 409 not queued/paused (running: use stop) |
| DELETE | `/api/queue/{id}` | **Deprecated alias of POST `/api/queue/{id}/cancel`** (same behaviour, does NOT delete). Response headers `Deprecation: true`, `Link: </api/queue/{id}/cancel>; rel="successor-version"` | same as cancel |
| DELETE | `/api/jobs/{id}` | HARD DELETE: job document, all its steps (prompts, responses, artifacts) and registered per-job data. `{deleted:true, job_id, previous_status, steps_deleted, was_queued}`; a queued/paused job is taken out of the queue (positions renumbered) | 404; 409 running ("stop it first, then delete it") |
| POST | `/api/jobs/bulk-delete` body `{job_ids:[...]}` (1-500, duplicates/blank ignored) | 200 `{deleted:[ids in request order], deleted_count, skipped:[{job_id, reason:"running"\|"not_found"}]}` | 422 missing/empty/over 500 ids |
| POST | `/api/jobs/delete-finished` | deletes every `done\|error\|stopped\|cancelled` job; same shape as bulk-delete | - |
| GET | `/api/jobs/{id}/transcript` | `{job_id,title,status,model,arena_url,provider,project_context,created_at,finished_at,step_total,steps_done,turns:[{step_index,step_title,status,prompt,response,error,artifact_paths,started_at,finished_at}]}` - one user (prompt) / assistant (response) turn per sent step, pending steps omitted | 404 |
| GET | `/api/jobs/{id}/transcript.html` | `text/html; charset=utf-8` attachment `roadmap2arena-<id8>-transcript.html`: standalone (inline CSS, no scripts/external assets), all text HTML-escaped, fenced blocks as `<pre>` | 404 |
| GET | `/api/jobs/{id}/files` | `[{path,step_index,versions:[step...],size,zip_path,zip_skip_reason}]` latest version per path (done steps), sorted by path | 404 |
| GET | `/api/jobs/{id}/files/download?path=<p>&step=<n>` | `text/plain; charset=utf-8` attachment (file name = basename); latest version, or the version from step n | 404 job/file/version; 422 missing path, step outside 1..100000 |
| GET | `/api/jobs/{id}/clone-source` | `{source_job_id,title,arena_url,model,provider,project_context,roadmap_md,step_total}` for the prefilled Clone form | 404 |
| GET | `/api/jobs/{id}/download` | `application/zip`, latest version per path | 404 unknown job; 409 no artifacts |
| GET | `/api/jobs/{id}/git?limit=1..500` | `{job_id, project_id, job_status, exists, can_init, repo_id, owner:{type,id}, path, default_branch, head, commit_count, uncommitted_steps, commits:[{sha, short_sha, message, step_index, author_name, author_email, date, files_changed, insertions, deletions}], remotes}` newest first; `exists:false` (no repo yet) has `commits:[]` | 404 job, 422 limit |
| POST | `/api/jobs/{id}/git/init` | create the repo now and commit every done step without a commit (older jobs); same shape + `committed_steps:[N]`; idempotent | 404, 500 git error |
| GET | `/api/jobs/{id}/git/commits/{sha}` (4-40 hex) | `{sha, short_sha, subject, message, parents, step_index, author_name, author_email, date, files:[{path, status added/modified/deleted/renamed, old_path, additions, deletions}], patch (max 400k chars), patch_truncated, diff}` - `diff` = structured diff (see below) | 422 bad sha, 404 job/repo/commit |
| GET | `/api/jobs/{id}/git/compare?head=<sha>&base=<sha>` (base optional = parent of head; 4-40 hex) | structured diff object (below) for comparing two steps | 422 bad sha, 404 job/repo/commit |
| GET | `/api/jobs/{id}/git/download?format=zip\|bundle` | zip = working tree + `.git` in `roadmap2arena-<id8>/` (`roadmap2arena-<id8>-repo.zip`); bundle = `git bundle --all` (`roadmap2arena-<id8>.bundle`, `git clone file.bundle`) | 404 job/no repo, 409 no commits, 422 format |
| GET | `/api/github` | `{connected, auth_method: pat\|oauth\|env\|null, source: settings\|env\|null, encryption: ok\|missing\|invalid, env_token, token_error, username, name, avatar_url, html_url, scopes, token_type, connected_at, auto_push:{enabled, private}, oauth:{available, client_id, client_id_source: env\|settings\|null, pending:{user_code, verification_uri, expires_at, interval}\|null}}` - never the token | - |
| PUT | `/api/github/token` `{token}` | validates with GitHub `GET /user` (scopes from X-OAuth-Scopes), stores it Fernet-encrypted; same shape as GET | 422 malformed, 401 rejected by GitHub, 409 no/invalid `R2A_SECRET_KEY` / OAuth connected / device flow pending / `R2A_GITHUB_TOKEN` set, 502 unreachable, 504 timeout |
| DELETE | `/api/github` | disconnect (token removed; auto_push and client id kept) | 409 when `R2A_GITHUB_TOKEN` is set |
| PUT | `/api/github/settings` `{auto_push?:{enabled?, private?}, oauth_client_id?}` | same shape as GET; `oauth_client_id:""` clears (falls back to `GITHUB_OAUTH_CLIENT_ID`) | 422 |
| POST | `/api/github/oauth/start` | device flow (scope `repo`): `{user_code, verification_uri, expires_at, interval}` (device_code stays server-side) | 409 connected / not configured / no `R2A_SECRET_KEY` / `R2A_GITHUB_TOKEN` set, 422 GitHub refused (unknown client id, device flow disabled), 502 |
| POST | `/api/github/oauth/poll` | `{status: pending\|slow_down\|connected\|expired\|denied, interval, github}`; calls GitHub at most once per interval; polling again after the OAuth flow completed returns 200 `connected` (idempotent) | 409 nothing pending, 409 connected with a personal access token (settings or R2A_GITHUB_TOKEN), 422 other OAuth error |
| POST | `/api/github/oauth/cancel` | clears the pending flow; GET shape | - |
| GET | `/api/github/repos?q=` | `{items:[{full_name, name, owner, private, default_branch, html_url, description, can_push, updated_at}], total, truncated}` (up to 300, most recently updated) | 409 not connected (or stored token undecryptable), GitHub errors per the mapping below |
| GET | `/api/jobs/{id}/github` | `{job_id, connected, username, defaults:{repo_name, branch: r2a/job-<id8>, pr_title, pr_body}, last_push, watches}` | 404 |
| POST | `/api/jobs/{id}/github/push` | body `{mode: new\|existing, repo_name, private (default true), description, repo_full_name, branch, open_pr, pr_base, pr_title, pr_body}` -> `{pushed, source, pushed_at, branch, head, commit_count, repo:{full_name, html_url, private, created}, branch_url, commit_url, pr:{number, html_url, existing, base, state}\|null, pr_error}`; emits `github_pushed` (+ `github_pr_opened` for a new PR) and upserts a watch | 404, 409 not connected / running / no commits / repo name exists / non-fast-forward branch / protected or rules, 422 validation (incl. open_pr with mode new; branch/pr_base must be valid as `refs/heads/<name>` (git check-ref-format --normalize, already normalised), no `@{` / bare `@` / `HEAD`, max 200 chars, no leading `-` or whitespace), 401 bad token, 403 no permission / workflow scope, 404 repo, 429 rate limit (Retry-After), 502, 504 push timeout |
| GET | `/api/github/watches` | `{items:[{job_id, full_name, branch, html_url, head_sha, pr_number, state:{pr_state, ci: none\|pending\|success\|failure, ...}, active, pushed_at, expires_at, last_error, last_polled_at}], poll_seconds, paused_for, pause_reason}` (no etags) | - |
| POST | `/api/github/poll` | poll the active watches now: `{watches, notifications}` or `{skipped: not_connected\|rate_limited\|rate_low\|auth, resume_in?}` | - |
| GET | `/api/notifications?limit=1..200&unread=bool` | `{items:[{id,event,job_id,title,status,step,message,url,created_at,read,deliveries}], unread_count, total}` newest first (default limit 50) | 422 bad limit/unread |
| POST | `/api/notifications/{id}/read` | `{id, read:true, unread_count}` | 404 |
| POST | `/api/notifications/read-all` | `{updated, unread_count:0}` | - |
| DELETE | `/api/notifications/{id}` | `{id, deleted:true}` | 404 |
| DELETE | `/api/notifications` | clear all: `{deleted, unread_count:0}` | - |
| GET | `/api/notifications/settings` | `{app_url, in_app:{events}, webhook:{enabled,url,events}, email:{enabled,host,port,security,username,from_addr,to_addrs,events,password_set,password_masked}, updated_at}` - the SMTP password is NEVER returned | - |
| PUT | `/api/notifications/settings` partial deep merge | same shape as GET | 422 unknown key, app_url/webhook.url not http(s), webhook enabled without url, port not int 1-65535, security not starttls/ssl/none, invalid from/to address, email enabled without host/from/to, events with unknown keys/non-bool, bad host |
| POST | `/api/notifications/test/webhook` body `{url?}` (optional, not saved) | `{ok, status_code, error, url}` - sends an `event:"test"` payload even if the webhook is disabled | 422 no url / bad url |
| POST | `/api/notifications/test/email` body = optional unsaved email fields (password omitted/masked = saved one) | `{ok, error, to_addrs}` | 422 invalid fields |

Statuses: job `queued|paused|running|done|error|stopped|cancelled`; step `pending|running|done|error|stopped`.
Finished (history) = `done|error|stopped|cancelled`; resumable = `error|stopped|cancelled`.
UI labels: done = "Completed", error = "Failed", queued = "Queued" (not started yet).
`cloned_from` (source job id or null) is on job detail, the job list and queue summaries.

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
- `POST /api/jobs` defaults model from settings (provider/URL resolution: see Providers). Each run (new, resumed,
  restarted) reads step delay and request timeout from settings when it starts and logs
  them ("..., step delay 2s, timeout 300s"); a running job keeps its values.
- The four public settings are unchanged. Optional `ARENA2API_API_KEY` is a
  server-only environment credential, not a settings/job field. It is sent only
  to the exact configured `ARENA2API_URL` after whitespace/trailing-slash
  normalisation; aliases, ports, paths and URL overrides receive no secret.
  Configured key values echoed in provider errors are redacted before persistence.
- Providers add no environment variables. `default_provider_id` / `providers_migrated` live in the same doc
  but are managed by the providers API, not `PUT /api/settings`.

### Providers (backend/providers.py, provider_routes.py)

Jobs can use any OpenAI-compatible chat API. A **provider** is a named base URL plus an
optional API key and optional extra headers; arena2api is one preset among others.

- MongoDB collection `providers`, one doc per provider:
  `id` (UUID4), `name` (1-60 chars, unique case-insensitively), `preset`
  (`openai|openrouter|groq|ollama|lmstudio|arena2api|custom`), `base_url` (http/https, the
  full OpenAI base **including** `/v1` or the provider's equivalent, e.g.
  `https://openrouter.ai/api/v1`; trailing `/` stripped), `api_key_enc` (Fernet token or
  null), `headers` (object name -> value, max 10), `default_model` (string or null),
  `migrated` (bool), `created_at`, `updated_at`. The default provider id is stored in the
  settings doc as `default_provider_id` (not part of `GET /api/settings`).
- **API key**: write-only. Encrypted with `R2A_SECRET_KEY` (same Fernet key and helper as the
  GitHub token). It is never returned, logged, stored on jobs, put in a URL, or echoed in an
  error: any provider error text is passed through a redactor that replaces the key (and
  any extra-header value) with `[redacted]` before it is stored or returned. Saving a key
  without a valid `R2A_SECRET_KEY` -> 409 with the key-generation hint. Providers without a
  key work without `R2A_SECRET_KEY`. If the stored key cannot be decrypted (key changed), the
  provider shows `api_key_error` and jobs using it fail with "re-enter the API key".
- **Extra headers**: plain, returned to the UI (e.g. OpenRouter `HTTP-Referer`, `X-Title`).
  Names must be RFC 7230 tokens; values printable ASCII, max 500 chars. Credential-like or
  managed names are refused with 422 ("use the API key field"): `authorization`,
  `proxy-authorization`, `cookie`, `x-api-key`, `api-key`, `host`, `content-length`,
  `content-type`, `connection`, `transfer-encoding`.
- Public shape (`GET`, create/update responses):
  `{id, name, preset, base_url, headers, default_model, api_key_set, api_key_error, server_key,
  is_default, migrated, created_at, updated_at}` (`api_key_error`: null or a message;
  `server_key`: true when the provider has no key of its own and will use the server-only
  `ARENA2API_API_KEY` - see "Server gateway key"; the key value is never returned).
- Presets (`GET /api/providers` -> `presets`), each `{id, label, base_url, needs_key, hint}`:
  OpenAI `https://api.openai.com/v1` (key), OpenRouter `https://openrouter.ai/api/v1` (key),
  Groq `https://api.groq.com/openai/v1` (key), Ollama `http://localhost:11434/v1`,
  LM Studio `http://localhost:1234/v1`, arena2api (local) `http://localhost:9090/v1`,
  Custom (empty).

Endpoints:

- `GET /api/providers` -> `{providers: [...], default_provider_id, presets: [...],
  encryption: "ok"|"missing"|"invalid"}` (providers sorted by name).
- `POST /api/providers` body `{name, preset?, base_url, api_key?, headers?, default_model?,
  make_default?}` -> 201 provider. The first provider ever created becomes the default.
- `PUT /api/providers/{id}` body: any of `name, preset, base_url, headers, default_model`;
  `api_key` (non-empty string replaces it; omitted = unchanged); `clear_api_key: true`
  removes it. -> provider. 404 unknown id, 422 validation (unknown fields too).
- `DELETE /api/providers/{id}` -> 204. 409 while a queued/paused/running job uses it. If it
  was the default, the default becomes null. Finished jobs keep their snapshot.
- `POST /api/providers/{id}/default` -> provider (now default).
- `GET /api/providers/{id}/models` -> `{models: [id...], count}` from `GET {base_url}/models`
  (OpenAI `{"data":[{"id":...}]}` or a bare list; sorted, de-duplicated, max 1000). Errors
  -> 502 `{"detail": "<mapped message>"}` (see error mapping) so the UI can fall back to a
  typed model name.
- `POST /api/providers/test` body `{base_url, api_key?, headers?, provider_id?}` -> always 200
  `{ok, status, model_count, models (first 200), latency_ms, message}`. Tests an unsaved
  draft; with `provider_id` and no `api_key` the stored key is used. Same request as
  "Fetch models"; the key is never echoed.
- Timeouts: 15 s for test/models.

Migration (startup, idempotent): when the `providers` collection is empty and the settings
doc has no `providers_migrated` flag, a provider `arena2api (local)` (preset `arena2api`,
`base_url` = settings `arena_url` + `/v1`, no key, `migrated: true`) is created and made the
default; the flag is then set, so deleting every provider does not recreate it. Existing jobs
are not rewritten: a job without `provider` is a **legacy job** and keeps using
`arena_url` + `/v1` with no key (also for resume/restart).

Jobs:

- `POST /api/jobs` accepts `provider_id` (optional). Resolution: `provider_id` -> that
  provider (422 if unknown); else an explicit `arena_url` -> legacy job (no provider, no key,
  as before); else the default provider; else (no default) legacy with settings `arena_url`.
  `model` defaults to the provider's `default_model`, else settings `model`.
- Job doc gains `provider` = non-secret snapshot `{id, name, preset, base_url}` or null.
  For provider jobs `arena_url` = the snapshot `base_url` (kept for compatibility/display).
  The key is never copied to the job.
- Runs use the provider's **current** base URL, key and headers (looked up by id). If the
  name/base URL/preset changed since the job was created, the job's snapshot and `arena_url`
  are refreshed and logged ("Provider changed since the job was created: A -> B"). If the
  provider was deleted, the run fails at step 1 with "Provider '<name>' was deleted - resume
  or restart the job with another provider"; an undecryptable key fails the same way.
- Key/host binding: a stored key is only sent to the host it was saved for. `PUT` that moves
  `base_url` to another origin (scheme/host/port) while a key is stored -> 422 unless the
  request also sets `api_key` or `clear_api_key`. `POST /providers/test` with `provider_id`
  reuses the stored key only for the stored origin (else 422 "Enter the API key...").
- Server gateway key (`ARENA2API_API_KEY`, from PR #11): stays server-only (never stored in
  Mongo, never returned, redacted from errors). Effective key per request:
  provider jobs, `GET /providers/{id}/models` and `POST /providers/test` use the provider's own
  key; a provider WITHOUT a key whose `base_url` is exactly `legacy_base(ARENA2API_URL)`
  (`ARENA2API_URL` + `/v1` after whitespace/trailing-slash normalisation - e.g. the migrated
  "arena2api (local)") uses `ARENA2API_API_KEY`. Legacy jobs keep PR #11's rule: the key only
  when the job's `arena_url` is exactly `ARENA2API_URL`. Aliases (127.0.0.1 vs localhost),
  other ports or paths never receive it.
- `POST /jobs/{id}/resume` and `/restart` accept `provider_id` too (switches provider and
  refreshes the snapshot). An `arena_url` override without `provider_id` turns the job into a
  legacy job (provider null) - unchanged behaviour for existing clients.
- `provider` is included in `GET /api/jobs` items, `GET /api/jobs/{id}`, queue summaries
  (`GET /api/queue` running/queued), `GET /jobs/{id}/transcript` and `clone-source`
  (clone-and-edit preselects it when it still exists). Logs say "provider <name> (<base_url>)".

Error mapping (`provider_errors.describe`, used by job runs, models and test). Every message
starts with `<name> returned <code>` (name = provider name, `arena2api` for legacy jobs),
then the provider's own short error text (max 200 chars, redacted), then a hint:

| Upstream | Hint |
| --- | --- |
| 401 | `(API key rejected) - check the API key in Settings > Providers`; for legacy jobs `arena2api returned 401: <detail> - check that ARENA2API_API_KEY in backend/.env matches GATEWAY_API_KEY in gateway/.env - the key is only sent when the job URL is exactly ARENA2API_URL` (PR #11 wording); preset arena2api providers get the same hint plus `, or set this provider's API key in Settings > Providers` |
| 403 | `(access denied) - the key may lack access to this model, or the account needs credits` |
| 404 | job: `- model '<model>' not found or wrong base URL; check the model name or fetch the model list`; models/test: `- endpoint not found; check the base URL (it usually ends in /v1)` |
| 429 | `(rate limit or quota exceeded) - wait, then resume the job` |
| 5xx | `(server error)`; for preset arena2api and legacy jobs a 503 keeps the hint "check that the arena2api Chrome tab is open and pushing tokens" |
| timeout | `<name> request timed out after Ns` |
| connect | `could not reach <name> at <base_url> - is it running?` |

Legacy base URL rule: `arena_url` + `/v1`, unless `arena_url` already ends in `/v1` (so a
provider job's `arena_url` can be reused as an override).

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

### Git history (backend/git_cli.py, repos.py, git_integration.py)

- Every job has its own local repo at `<R2A_DATA_DIR>/repos/<job_id>` (default data dir
  `<repo root>/data`, gitignored - deliberately OUTSIDE `backend/` so uvicorn --reload never sees
  job files, F-006), branch `main`, created when the job's first run starts. On startup repos
  left in the old `backend/data/repos` are moved there (only when the new path does not exist;
  `R2A_MIGRATE_LEGACY_DATA=0` disables it, test servers do). Dev reload watches only `backend/`
  minus tests/, data/ and git files: `deploy/supervisor/backend.conf`, `run.sh` (needs watchfiles).
- After each completed step the step's artifacts are written into the working tree (files
  accumulate across steps, latest version wins, same as the ZIP) and committed as
  `Step N: <title>` with author and committer `ROADMAP2ARENA <roadmap2arena@localhost>`. The body
  holds trailers `R2A-Job: <id>` and `R2A-Step: N`. A step without named files gives an empty commit.
  The sha is stored on the step (`commit_sha`, also in GET /api/jobs/{id} steps).
- Paths are normalised like the ZIP (leading `/` and `./` dropped); paths with `..` or a `.git`
  segment, paths through symlinks and file/directory clashes are skipped and logged
  (`Git: skipped "<path>" - <reason>`).
- Resume continues the same repo (done steps keep their commits). Restart and clone are new jobs
  and get a new repo. Every run first commits done steps that have no commit yet (sync).
  Deleting a job deletes its repo (directory + `repos` document).
- Git failures are logged (`Git: commit failed - ...`, warn) and never fail the job.
- git runs via subprocess with argument lists (no shell), no system/global config, hooks off,
  no prompts, `protocol.ext` disabled.
- Data model (ready for projects): `repos` collection
  `{id, owner:{type:"job"|"project", id}, kind:"local", path, default_branch, head, commit_count,
  remotes:[...], created_at, updated_at}`; jobs carry `repo_id` and `project_id` (null = standalone).
  `repos.snapshot(repo, ref="HEAD", include, exclude, budget_bytes, max_file_bytes)` returns the
  tree and text file contents at a ref within a byte budget (for future project context).
- Structured diff (`backend/git_diff.py`, `diff_sync(root, base, head)`): `{base, head, files:[{path,
  old_path, status, additions, deletions, binary, patch, patch_too_large, old_content, new_content,
  context_expandable}], truncated, stats:{files, additions, deletions}}`; old/new content lets the UI
  expand hidden context. Caps: 300 files, 200k chars per patch / 2M total, 256 kB per content / 3 MB total.
- UI: History detail > Git tab: commit list, commit detail rendered with the DiffViewer,
  "Compare steps" (From/To selects -> /git/compare), Download repo (zip incl. .git) and Bundle;
  "Create repository" for jobs without a repo.
- DiffViewer (`frontend/src/components/r2a/DiffViewer.jsx`, lazy chunk, `@git-diff-view/react`, MIT):
  split/unified toggle (localStorage `r2a.diffMode`, unified by default below 640px), wrap toggle,
  file list with octicon status icons and +/- counts, collapsible file sections, expand/collapse all,
  syntax highlighting (lowlight), word-level change highlighting, expandable context lines,
  error boundary falling back to the raw patch.
- Icon pass: every action button has a lucide/octicon icon; icon-only buttons carry aria-label.

### GitHub (backend/github_client.py, github_integration.py)

- Connect with a personal access token OR OAuth (device flow; `GITHUB_OAUTH_CLIENT_ID` in .env or a
  client ID saved in Settings). Mutually exclusive: connecting one while the other is active is 409
  (replacing a PAT with another PAT is allowed); a PAT is also refused while a device code is pending.
- The token (PAT or OAuth alike) is stored Fernet-encrypted (`token_enc`) in Mongo (`integrations`,
  `_id: "github"`) with `R2A_SECRET_KEY` from backend/.env; saving without a valid key is a 409 with
  the key-generation hint. A changed key makes the stored token unreadable (`token_error`, reconnect).
  A plaintext token from a pre-release build is encrypted on first read. `R2A_GITHUB_TOKEN` in
  .env overrides everything (`auth_method: env`; connect/disconnect from the UI are 409).
- The token is never returned, logged, put in argv, the remote URL or .git/config: `git push` gets it
  as `http.<https://host/>.extraHeader` (`Authorization: Basic base64(login:token)`, an empty value
  first resets inherited headers, `credential.helper` cleared) through `GIT_CONFIG_COUNT/KEY_n/VALUE_n`
  env vars of that one process. All git output is passed through `redact()` (the token and its
  base64 form, `github_pat_*`, `gh[pousr]_*`, Authorization values, URL credentials). Push timeout
  300 s. Only https clone URLs on github.com (or the configured API host) are pushed to
  (`R2A_GITHUB_ALLOW_FILE_REMOTES=1` allows local paths, for tests only).
- REST: raw httpx, `X-GitHub-Api-Version: 2026-03-10`, timeouts 20 s read / 10 s connect, serial
  requests. GETs retry once on 5xx/network errors; POSTs never retry. Mapping: 401 -> 401; 403 ->
  403 with `X-Accepted-GitHub-Permissions` or accepted/actual OAuth scopes; 403/429 rate limit ->
  429 + `Retry-After`; 404 -> 404 "not found or no access"; 409/422/451 pass through; timeout -> 504;
  network/5xx/other -> 502. git push failures: non-fast-forward 409, workflow permission 403, auth
  401, repository not found 404, permission denied 403, GH013/protected/rules 409, timeout 504.
- Watcher (polling, no public URL/webhook): each push upserts `github_watches` `{job_id, full_name,
  branch, head_sha, pr_number, etags, state, active, pushed_at, expires_at (+7 days), last_error}`.
  An in-process loop (started in lifespan; keep uvicorn `--workers 1`) runs every
  `R2A_GITHUB_POLL_SECONDS` (default 60, min 30), right after a push and on `POST /api/github/poll`.
  ETag conditional GETs on the branch, the PR (`merged`, never `merge_commit_sha`), the combined status
  and check-runs (on 403/404 falls back to `actions/runs?head_sha=`). Emits `github_pushed` (new
  commits from outside), `github_pr_merged`, `github_pr_closed`, `github_checks_passed`,
  `github_checks_failed`. A watch ends after 7 days or once the PR is closed/merged and CI is final
  ("no CI" counts as final 15 min after the push). Rate limit/auth errors pause polling
  (Retry-After / reset / 60 s; 10 min for auth; also when fewer than 50 calls remain); reconnecting
  lifts the pause. Deleting a job deletes its watches (never anything on GitHub).
- Push: syncs the job repo first, then `git push HEAD:refs/heads/<branch>` - never `--force`; a
  diverged branch is a 409 asking for another branch name. New repos are created with
  `auto_init:false`; a PR needs an existing repo (base = `pr_base` or the repo's default branch).
  PR problems after a successful push come back as `pr_error` (unrelated history, missing base,
  nothing to compare); an already-open PR for the branch is returned with `existing: true`.
- Each push is stored on the job (`github`, = last_push) and in the repo document's `remotes`
  (one entry per repo+branch, with `pushed_head`), and logged (`GitHub: pushed N commits to ...`).
- Auto-push (Settings): when a job finishes as done, push to its last pushed repo/branch, else a new
  repo `<slug>-<id6>` (private per setting). Failures are logged (`GitHub: auto-push failed - ...`).
- Tests use `GITHUB_API_URL` / `GITHUB_OAUTH_URL` pointing at backend/tests/github_stub.py.
- UI: Settings > GitHub (OAuth card with code + verification link, PAT card, each disabled with a
  note while the other is connected; missing-key banner with the generate command; .env-token note;
  "encrypted at rest"; Disconnect; auto-push; watched branches with CI/PR state and "Check now"). History detail: "Push to GitHub" sheet
  (new/existing repo with search, branch, optional PR with prefilled title/body, result links);
  the Git tab lists pushed remotes. Octicons for GitHub/git actions; the GitHub mark
  (`MarkGithubIcon`) is used unaltered in `currentColor`, always next to the word "GitHub", only on
  GitHub controls (GitHub logo guidelines) - never as the app logo.

### Notifications (backend/notifier.py, notify_settings.py, notification_routes.py)

- Events: `job_done`, `job_failed`, `job_stopped` (orchestrator `on_job_finished` hook, fired once
  when a run ends done/error/stopped) and `queue_empty` (right after a job ends, when no job is
  queued or running; paused jobs don't count and are mentioned: "(1 paused job left)").
  Server-restart interruptions and cancels do not notify. GitHub events (`github_pushed`,
  `github_pr_opened`, `github_pr_merged`, `github_pr_closed`, `github_checks_passed`,
  `github_checks_failed`) come from pushes and the watcher, with a prebuilt message and `link`
  (the GitHub branch/PR/commit URL; `url` stays the in-app job link).
- Channels with per-event toggles: `in_app` (stored in Mongo `notifications`, last 500 kept ->
  bell, toasts and browser notifications), `webhook`, `email`. Defaults: in-app all on; webhook
  off (all events on); email off (done + failed on; GitHub events off).
- Webhook: `POST <url>` JSON `{event, job_id, title, status, step, message, url, link, timestamp, app,
  text, content}`; `text` (Slack) = `content` (Discord, max 1900 chars) = emoji + message + link.
  `step` = step_total (done), failed_step (failed), stopped_step (stopped), null (queue empty).
  `url` = `<app_url>/?tab=history&job=<id>` (queue empty: `?tab=queue`). 10 s timeout, 2xx = ok.
- Email: SMTP via smtplib in a worker thread (15 s timeout), security `starttls` (requires the
  server's STARTTLS), `ssl` or `none`; login only if a username is set. Subject
  `[ROADMAP2ARENA] Job finished: <title>`, plain-text body with the link.
- Delivery results are written to the job log (`Notification: webhook delivered (HTTP 200)` /
  `Notification: email failed - ...`, level warn) and to the notification's `deliveries`. Failures
  never change the job or the queue.
- The SMTP password is stored server-side and is write-only: GET returns `password_set` and
  `password_masked` (`********`); PUT `password` omitted or `********` = keep, `""` = clear.
- Notifications of a deleted job are kept (their link then shows "Could not load job").
- UI: bell in the header (unread badge, panel with mark read / mark all read / remove / Clear
  (two-click)); clicking an item marks it read and opens the job (or the queue). New
  notifications appear as toasts (they replace the old queue-poll "Job finished"/"failed"
  toasts). Browser notifications: Settings toggle, per browser (localStorage
  `r2a.browserNotifications`), shown only while the tab is hidden/unfocused and permission is
  granted. Settings > Notifications: event x channel matrix, webhook URL + Send test, SMTP
  fields + Send test, app URL.

### Cancel vs delete

- **Cancel** (`POST /api/queue/{id}/cancel`, UI: Job queue "Cancel job") only applies to
  queued/paused jobs: status becomes `cancelled`, the job stays in history and can be
  resumed or restarted. `DELETE /api/queue/{id}` is kept as a deprecated alias.
- **Delete** (`DELETE /api/jobs/{id}`, bulk, delete-finished; UI: History detail "Delete",
  History list "Select" mode with "Delete selected" and "Delete all finished") removes the
  job for good. Running jobs are refused (409 / skipped "running"); queued/paused jobs are
  removed from the queue. Deletion runs under the scheduler lock, so a queued job cannot start
  while being deleted. Jobs that link to a deleted job (`restarted_from`/`cloned_from`) keep
  the id; opening it shows "Could not load job ... not found" (no retry toast).
- UI deletes use a two-click confirm (first click arms the button for 4 s: "Delete
  permanently?" / "Delete N jobs?"; blur, Escape or timeout disarms).
- Extension point: `deletion_routes.on_delete` callbacks `(db, job_id)` run after a delete
  (failures are logged, never block the delete).

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

## Gateway dependency integration

- `services/arena2api` is the unmodified Git submodule from
  `https://github.com/flay-o/arena2api.git`, pinned at
  `259e27c2a96c8203cfe6d67140490b0db9f91543`. No declared upstream license was
  found at this revision; no license grant or relicensing is implied.
- `./run-gateway.sh` / `python -m gateway` starts it independently of MongoDB and
  the job API, with loopback :9090, one worker and no reload by default. Startup
  verifies the local pin without fetching. `--check` validates/imports without
  a listener or Arena request and reports only whether an API key is configured.
- Optional `gateway/.env` uses `GATEWAY_HOST`, `GATEWAY_PORT`, `GATEWAY_LOG_LEVEL`,
  `GATEWAY_API_KEY`; process environment and CLI host/port overrides take precedence.
  Prefixed settings are mapped to upstream at import time; unrelated `API_KEY`,
  `PORT`, `DEBUG` are restored afterwards.
- `GATEWAY=1 ./run.sh` owns the gateway plus the existing job API and dashboard;
  `STUB=1` uses canned responses instead. The flags are mutually exclusive. With
  neither set, the gateway must already be running. Ubuntu `setup.sh` has an
  opt-in independent `<APP_NAME>-gateway.service`; browser setup stays manual.
- Chrome/Firefox extensions are loaded manually from the submodule. They push
  session data/models to the private gateway; keep an authorised Arena tab open.
  Server HTTP health is not equivalent to Arena readiness. Before connection,
  models contain `waiting-for-extension`, and chat returns 503. Disconnection
  occurs after 120 seconds without a push; model IDs come from the discovered cache.
- API-key protection applies **only** to `GET /v1/models` and
  `POST /v1/chat/completions`. `/health`, `/v1/extension/status` and
  `POST /v1/extension/push` remain unauthenticated; CORS is broad. Keep the service
  private even with a key. Caddy does not proxy gateway endpoints. Use a private
  loopback tunnel for a remote browser; the manifests do not allow arbitrary
  HTTPS preview origins.
- See [gateway setup/security](docs/arena2api.md) and
  [local integration evidence](docs/arena2api-integration-2026-10-02.md). Actual
  browser pairing/Arena delivery and Ubuntu provisioning have not been verified.

## Backend implementation

- `settings.py`: the original seven required settings from `backend/.env`
  (MONGO_URL, DB_NAME, ARENA2API_URL, ARENA2API_MODEL, ARENA_STEP_DELAY_SECONDS,
  ARENA_REQUEST_TIMEOUT_SECONDS, CORS_ORIGINS); missing values fail at startup.
  Optional `ARENA2API_API_KEY` is server-only and validates printable ASCII.
- `server.py`: routes, validation, startup indexes (`jobs.id` unique,
  `steps(job_id,index)` unique), marks leftover running jobs/steps as error
  "interrupted by server restart".
- `orchestrator.py`: one asyncio task per job, writes every state change to
  Mongo (motor), fail-fast, `ARENA_STEP_DELAY_SECONDS` pause between steps,
  503 errors carry the hint "check that the arena2api Chrome tab is open and
  pushing tokens".
- `arena_client.py`: AsyncOpenAI(base_url = the resolved provider base URL (legacy
  jobs: `{arena_url}/v1`), api_key = the provider's decrypted key or `sk-no-key`, extra
  provider headers, timeout from settings, max_retries 0), temperature 0.2, no streaming,
  rolling chat history per job; prompt formats match the former mock builder.
  `orchestrator.describe_error` -> `providers.describe`, which redacts the provider key,
  header values and the configured `ARENA2API_API_KEY` (as `[redacted gateway key]`) before
  truncating and storing errors.
- `artifact_extractor.py`: `BLOCK_RE` for fenced blocks, `lang:path` info string
  or `PATH_COMMENT_RE` first-line comment; `clean_zip_path` for the ZIP.
- Collections: `jobs` (job doc + log array), `steps` (one doc per step with
  prompt, response, artifacts list).

## Frontend integration

- `src/lib/api.js` is the only data layer: fetch to
  `${VITE_BACKEND_URL}/api/...`, GET retries (2, exponential backoff) on network
  errors/5xx, readable `ApiError` messages. Development and Caddy builds use an
  empty browser base URL: same-origin `/api` requests. Vite's server-side proxy
  targets `R2A_BACKEND_TARGET` or loopback :8001; it does not proxy gateway routes.
  `R2A_DEV_HOST=0.0.0.0` is available for restricted remote previews.
- `useJob` polls `GET /api/jobs/{id}` every 1.5 s while running/queued/paused;
  transient errors keep the last state and retry. `useQueue` polls `GET /api/queue`
  every 2 s (header status + progress, queue badge, Current job).
- Current job shows the running job; when it ends and nothing else runs it resets to
  "Waiting for next job", and the next started job appears automatically (queue poll).
  Finish/fail toasts are global (App, from the queue poll).
- Job history: collapsible side panel (narrow rail of status icons when collapsed; state
  in localStorage `r2a.historyPanel.collapsed`) listing up to 200 jobs of every status
  (polled every 4 s, search + status filter). The detail view (`?tab=history&job=<id>`)
  has Clone job (Sheet with the prefilled editable form -> `POST /api/jobs` with
  `cloned_from`), Export transcript (HTML download), Download ZIP, Resume/Restart/Stop,
  and Transcript (chat view from `GET /transcript`) | Files (view, copy, per-file
  download) | Steps | Log tabs.
- Top-level tabs: Create job | Job queue | Current job | Job history | Settings.
  URL keeps place: `?tab=create|queue|current|history|settings&job=<id>` (job only for
  history); a bare `/?job=<id>` opens that job in Job history. The Recent jobs sheet
  was replaced by Job history.
- Artifact contents and transcript prompt/response are fetched on demand via
  `GET /steps/{index}` (finished steps cached).
- ZIP via `GET /download`; filename from Content-Disposition.
- `/?tab=history&job=<id>` deep-links to a job.
- Providers (`hooks/useProviders.js`: one shared store for `GET /api/providers`, plus a
  per-session model cache): Settings > Providers lists providers (default badge, preset,
  base URL, "key saved", key errors) with Add / Edit / Make default / Delete (two-click
  confirm). The editor has a preset select (prefills name + base URL, shows the hint), a
  write-only API key (password field; an existing key shows "Key saved (hidden)" with
  Replace / Remove), extra headers rows, a default model (datalist after Fetch models),
  Test connection (`POST /providers/test`) and the R2A_SECRET_KEY warning. The runtime
  settings form no longer edits the arena2api URL (it is kept as the legacy fallback).
- Create job / Clone / Resume-Restart overrides use `ProviderModelFields`: provider select
  (default preselected) + model input with a datalist from `GET /providers/{id}/models`
  (fetched when the provider is picked, "Fetch models" refreshes; on failure the model is
  typed, free text always allowed). Clone keeps the source provider (deleted -> default with
  a note); legacy jobs offer "Legacy arena2api URL". Queue, history list/detail and Current
  job show "model via <provider name>" (legacy jobs: "arena2api").

## Testing

- `test-integration.sh` / `backend/tests/integration.py`: explicit disposable real
  MongoDB gate; optional ephemeral Compose MongoDB, no normal `.env`/database
  fallback. Runs self-contained checks, the new real-gateway job pipeline, and
  isolated deletion/notifications/Git/GitHub/reload regressions. Reload probes
  execute in a temporary source copy, not the user's checkout. CI is prepared in
  `.github/workflows/integration.yml`. Verified against a real throwaway MongoDB
  8.0 via `TEST_MONGO_URL` (2026-10-03); the Compose path and CI have not run yet. See [validation guide](docs/integration-validation.md).
- `backend/tests/test_gateway_pipeline.py`: seven prepared cases against real
  Mongo + backend + SDK + pinned gateway with only Arena HTTP mocked. Persistence,
  artifacts/Git/export, fail-fast/resume, disconnected recovery, credential trust,
  stop/resume, queue and deletion; no real provider. Passed 7/7 against a real
  throwaway MongoDB 8.0 (2026-10-03).
- `backend/tests/test_gateway.py`: actual pinned gateway + real OpenAI SDK with
  ASGI and mocked Arena transport; synthetic session/model data only. Tests local
  configuration, credential boundaries/redaction, auth, disconnected readiness,
  model discovery, SSE/nonstream conversion, chat history and artifact extraction,
  and a short-lived standalone listener without MongoDB. No real Arena request.
- `frontend/tests/dev_proxy.py`: real Vite with a local fixture HTTP receiver;
  checks preview Host acceptance, same-origin GET/POST and server-only API target.
  It does not replace the MongoDB-backed job lifecycle regression.
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
