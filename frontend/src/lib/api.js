// Data access layer for the ROADMAP2ARENA frontend.
// Talks to the FastAPI backend at `${VITE_BACKEND_URL}/api`. Components only use
// these functions, never fetch() directly.

import { guessLang } from './lang'

const BASE = `${(import.meta.env.VITE_BACKEND_URL || '').replace(/\/+$/, '')}/api`
const GET_RETRIES = 2
const HINT_503 = 'arena2api returned 503 - check that the arena2api Chrome tab is open and pushing tokens'

export class ApiError extends Error {
  constructor(message, { status = 0, network = false } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.network = network
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function errorFromResponse(res) {
  let detail = ''
  try {
    const body = await res.json()
    detail = Array.isArray(body.detail)
      ? body.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ')
      : body.detail || ''
  } catch {
    // non-JSON error body
  }
  return new ApiError(detail || `Request failed with HTTP ${res.status}`, { status: res.status })
}

// fetch wrapper: JSON in/out, readable errors, retries idempotent GETs on
// network errors and 5xx responses.
async function request(path, { method = 'GET', body, raw = false } = {}) {
  const attempts = method === 'GET' ? GET_RETRIES + 1 : 1
  let lastErr
  for (let i = 0; i < attempts; i += 1) {
    if (i > 0) await sleep(400 * 2 ** (i - 1))
    let res
    try {
      res = await fetch(`${BASE}${path}`, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch {
      lastErr = new ApiError(`Cannot reach the backend at ${BASE} - is it running?`, { network: true })
      continue
    }
    if (res.ok) return raw ? res : res.json()
    lastErr = await errorFromResponse(res)
    if (res.status < 500) break
  }
  throw lastErr
}

export const backendUrl = BASE

// ---------------------------------------------------------------- step cache
// Finished steps never change, so their full detail is cached per job.
const stepCache = new Map()
const stepKey = (jobId, index) => `${jobId}:${index}`

export async function getStep(jobId, index) {
  const key = stepKey(jobId, index)
  if (stepCache.has(key)) return stepCache.get(key)
  const step = await request(`/jobs/${encodeURIComponent(jobId)}/steps/${index}`)
  if (step.status === 'done' || step.status === 'error') stepCache.set(key, step)
  return step
}

export async function getArtifactContent(jobId, stepIndex, path) {
  const step = await getStep(jobId, stepIndex)
  const found = (step.artifacts || []).find((a) => a.path === path)
  if (!found) throw new ApiError(`${path} not found in step ${stepIndex}`)
  return found.content
}

// ---------------------------------------------------------------- adapters
function toJobView(dto) {
  const latest = {}
  for (const s of dto.steps) {
    for (const path of s.artifact_paths || []) {
      const prev = latest[path]
      latest[path] = {
        path,
        lang: guessLang(path),
        step_index: s.index,
        history: prev ? [...prev.history, s.index] : [s.index],
      }
    }
  }
  const hint = dto.status === 'error' && /\b503\b/.test(dto.error || '') ? HINT_503 : null
  return {
    id: dto.job_id,
    status: dto.status,
    title: dto.title,
    created_at: dto.created_at,
    finished_at: dto.finished_at,
    config: { arena_url: dto.arena_url, model: dto.model },
    project_context: dto.project_context,
    roadmap_md: dto.roadmap_md,
    step_total: dto.step_total,
    steps_done: dto.steps_done,
    error: dto.error,
    error_hint: hint,
    failed_step: dto.failed_step,
    stopped_step: dto.stopped_step ?? null,
    restarted_from: dto.restarted_from ?? null,
    queue_position: dto.queue_position ?? null,
    queued_at: dto.queued_at ?? null,
    started_at: dto.started_at ?? null,
    steps: dto.steps.map((s) => ({ ...s, files: s.artifact_paths || [] })),
    artifacts: Object.values(latest).sort((a, b) => a.path.localeCompare(b.path)),
    log: dto.log || [],
  }
}

// ---------------------------------------------------------------- endpoints
export async function getConfig() {
  return request('/config')
}

// Returns { steps, title }; a roadmap without steps yields steps: [].
export async function parseRoadmap(markdown) {
  if (!markdown.trim()) return { steps: [], title: null }
  try {
    return await request('/roadmap/parse', { method: 'POST', body: { roadmap_md: markdown } })
  } catch (err) {
    if (err.status === 422) return { steps: [], title: null }
    throw err
  }
}

// Enqueues a job. Resolves to { id, status: 'running'|'queued', queue_position }.
export async function startJob({ arena_url, model, project_context, roadmap_md }) {
  const res = await request('/jobs', { method: 'POST', body: { arena_url, model, project_context, roadmap_md } })
  return { id: res.job_id, status: res.status, queue_position: res.queue_position }
}

export async function getJob(id) {
  return toJobView(await request(`/jobs/${encodeURIComponent(id)}`))
}

// options: { status: ['done','error'], limit: 1..200 }
export async function listJobs({ status, limit } = {}) {
  const qs = new URLSearchParams()
  if (status?.length) qs.set('status', status.join(','))
  if (limit) qs.set('limit', String(limit))
  const list = await request(`/jobs${qs.size ? `?${qs}` : ''}`)
  return list.map((j) => ({
    id: j.job_id,
    status: j.status,
    created_at: j.created_at,
    finished_at: j.finished_at ?? null,
    started_at: j.started_at ?? null,
    restarted_from: j.restarted_from ?? null,
    title: j.title || 'Untitled roadmap',
    model: j.model,
    steps_done: j.steps_done,
    step_total: j.step_total,
    failed_step: j.failed_step,
    stopped_step: j.stopped_step ?? null,
  }))
}

// ---------------------------------------------------------------- job controls
function overridesBody(overrides) {
  const body = {}
  if (overrides?.arena_url?.trim()) body.arena_url = overrides.arena_url.trim()
  if (overrides?.model?.trim()) body.model = overrides.model.trim()
  return Object.keys(body).length ? body : undefined
}

function forgetSteps(jobId) {
  for (const key of [...stepCache.keys()]) if (key.startsWith(`${jobId}:`)) stepCache.delete(key)
}

// Stops a running job. Resolves to { job_id, status, stopped_step, steps_done }.
export async function stopJob(id) {
  return request(`/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST' })
}

// Continues the same job from its first unfinished step (error/stopped jobs).
export async function resumeJob(id, overrides) {
  forgetSteps(id)
  return request(`/jobs/${encodeURIComponent(id)}/resume`, { method: 'POST', body: overridesBody(overrides) })
}

// Creates a new (queued) job from a finished one. Resolves to { id, status, queue_position }.
export async function restartJob(id, overrides) {
  const res = await request(`/jobs/${encodeURIComponent(id)}/restart`, { method: 'POST', body: overridesBody(overrides) })
  return { id: res.job_id, status: res.status, queue_position: res.queue_position }
}

// ---------------------------------------------------------------- queue
// { running: summary|null, queued: [summary], count, waiting }
export async function getQueue() {
  return request('/queue')
}

export async function moveQueued(id, direction) {
  return request(`/queue/${encodeURIComponent(id)}/move`, { method: 'POST', body: { direction } })
}

export async function pauseQueued(id) {
  return request(`/queue/${encodeURIComponent(id)}/pause`, { method: 'POST' })
}

export async function unpauseQueued(id) {
  return request(`/queue/${encodeURIComponent(id)}/unpause`, { method: 'POST' })
}

export async function removeQueued(id) {
  return request(`/queue/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------- settings
export async function getSettings() {
  return request('/settings')
}

export async function saveSettings(values) {
  return request('/settings', { method: 'PUT', body: values })
}

export async function resetSettings() {
  return request('/settings/reset', { method: 'POST' })
}

function filenameFrom(res, fallback) {
  const cd = res.headers.get('Content-Disposition') || ''
  const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  return m ? decodeURIComponent(m[1]) : fallback
}

// Downloads the job ZIP built by the backend and saves it in the browser.
export async function downloadZip(jobId) {
  const res = await request(`/jobs/${encodeURIComponent(jobId)}/download`, { raw: true })
  const blob = await res.blob()
  const filename = filenameFrom(res, `roadmap2arena-${jobId}.zip`)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return { filename, size: blob.size }
}
