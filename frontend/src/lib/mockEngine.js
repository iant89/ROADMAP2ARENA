// In-browser simulation of the ROADMAP2ARENA backend job runner.
// Only src/lib/api.js should import this module.

import {
  CANNED_RESPONSES, GENERIC_RESPONSE_TEMPLATES, MOCK_ERROR, SAMPLE_JOBS, SAMPLE_ROADMAP,
} from '@/mock'
import { parseRoadmap } from './roadmapParser'
import { buildPrompt } from './prompts'
import { extractBlocks, mergeArtifacts } from './artifacts'

const STORAGE_KEY = 'r2a.jobs.v1'
const MAX_STORED = 20
const memory = new Map() // jobs created in this tab (live objects)
const timers = new Map()

const nowIso = () => new Date().toISOString()
const slugify = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'step'
const isSampleRoadmap = (md) => md.trim() === SAMPLE_ROADMAP.trim()

export function cannedResponse(roadmapMd, step) {
  if (isSampleRoadmap(roadmapMd) && CANNED_RESPONSES[step.index - 1]) {
    return CANNED_RESPONSES[step.index - 1]
  }
  const tpl = GENERIC_RESPONSE_TEMPLATES[(step.index - 1) % GENERIC_RESPONSE_TEMPLATES.length]
  return tpl({ title: step.title, index: step.index, slug: `step${step.index}_${slugify(step.title)}` })
}

function summarizeBlocks(response) {
  const blocks = extractBlocks(response)
  const named = blocks.filter((b) => b.path)
  return { files: named.map((b) => b.path), unnamed: blocks.length - named.length }
}

function filesNote({ files, unnamed }, replaced) {
  let note = `${files.length} file${files.length === 1 ? '' : 's'}`
  const extras = []
  if (unnamed) extras.push(`${unnamed} unnamed block${unnamed === 1 ? '' : 's'} kept in transcript`)
  if (replaced.length) extras.push(`replaced ${replaced.join(', ')}`)
  if (extras.length) note += ` (${extras.join('; ')})`
  return note
}

function blankJob({ id, created_at, config, project_context, roadmap_md }) {
  const steps = parseRoadmap(roadmap_md).map((s) => ({
    ...s, status: 'pending', prompt: '', response: '', error: null,
    started_at: null, finished_at: null, files: [],
  }))
  return {
    id, created_at, finished_at: null, status: 'running', config, project_context, roadmap_md,
    step_total: steps.length, steps_done: 0, steps, artifacts: {}, log: [],
    error: null, error_hint: null, failed_step: null,
  }
}

function previousPaths(job) {
  return Object.keys(job.artifacts).sort()
}

function applyStepDone(job, step, response, ts) {
  const before = job.artifacts
  const summary = summarizeBlocks(response)
  const replaced = summary.files.filter((p) => before[p]).map((p) => `${p} from step ${before[p].step_index}`)
  job.artifacts = mergeArtifacts(before, step.index, response)
  Object.assign(step, { status: 'done', response, finished_at: ts, files: summary.files })
  job.steps_done += 1
  return filesNote(summary, replaced)
}

// Builds a full job snapshot from a SAMPLE_JOBS entry.
function hydrateSample(sample) {
  const base = new Date(sample.created_at).getTime()
  const at = (sec) => new Date(base + sec * 1000).toISOString()
  const job = blankJob(sample)
  job.status = sample.status
  job.finished_at = sample.finished_at
  job.log = sample.log.map(([sec, level, msg]) => ({ ts: at(sec), level, msg }))
  const stopAt = sample.failed_step ?? job.step_total + 1
  for (const step of job.steps) {
    if (step.index > stopAt) break
    step.prompt = buildPrompt({
      projectContext: job.project_context, steps: job.steps, stepIndex: step.index,
      previousFiles: previousPaths(job),
    })
    step.started_at = job.created_at
    if (step.index === sample.failed_step) {
      Object.assign(step, { status: 'error', error: sample.error, finished_at: sample.finished_at })
      continue
    }
    applyStepDone(job, step, cannedResponse(job.roadmap_md, step), job.created_at)
  }
  if (sample.status === 'error') {
    job.error = sample.error
    job.failed_step = sample.failed_step
    job.error_hint = MOCK_ERROR.hint
  }
  return job
}

const SAMPLES = SAMPLE_JOBS.map(hydrateSample)

function readStored() {
  try {
    const list = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

function writeStored(list) {
  try {
    const sorted = [...list].sort((a, b) => b.created_at.localeCompare(a.created_at))
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sorted.slice(0, MAX_STORED)))
  } catch {
    // Storage full or unavailable: the job still lives in memory.
  }
}

function persist(job) {
  const list = readStored().filter((j) => j.id !== job.id)
  list.unshift(job)
  writeStored(list)
}

// A stored job marked running that is not alive in this tab was interrupted.
function reviveStored(job) {
  if (job.status !== 'running' || memory.has(job.id)) return job
  const fixed = structuredClone(job)
  fixed.status = 'error'
  fixed.error = 'Interrupted - the page was reloaded while this simulated job was running'
  const running = fixed.steps.find((s) => s.status === 'running')
  if (running) {
    running.status = 'error'
    running.error = fixed.error
    fixed.failed_step = running.index
  }
  return fixed
}

export function getAllJobs() {
  const stored = readStored().map(reviveStored)
  const live = [...memory.values()]
  const byId = new Map()
  for (const j of [...live, ...stored, ...SAMPLES]) if (!byId.has(j.id)) byId.set(j.id, j)
  return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

export function findJob(id) {
  return memory.get(id) || getAllJobs().find((j) => j.id === id) || null
}

export function hasRunningJob() {
  return [...memory.values()].some((j) => j.status === 'running')
}

function log(job, level, msg) {
  job.log.push({ ts: nowIso(), level, msg })
}

export function createJob({ config, project_context, roadmap_md }) {
  if (hasRunningJob()) throw new Error('A job is already running - wait for it to finish')
  const id = `job-${Date.now().toString(36)}`
  const job = blankJob({ id, created_at: nowIso(), config, project_context, roadmap_md })
  if (!job.step_total) throw new Error('No steps found in the roadmap')
  memory.set(id, job)
  log(job, 'info', `Job ${id} created (${job.step_total} steps, model ${config.model})`)
  persist(job)
  runStep(job, 1)
  return job
}

function runStep(job, index) {
  const step = job.steps[index - 1]
  step.status = 'running'
  step.started_at = nowIso()
  step.prompt = buildPrompt({
    projectContext: job.project_context, steps: job.steps, stepIndex: index,
    previousFiles: previousPaths(job),
  })
  const base = job.config.arena_url.replace(/\/+$/, '')
  log(job, 'info', `POST ${base}/v1/chat/completions - step ${index} "${step.title}"`)
  persist(job)

  const delayMs = Math.max(0.3, Number(job.config.step_delay_seconds) || 2) * 1000
  const jitter = Math.round(Math.random() * 400)
  timers.set(job.id, setTimeout(() => {
    const note = applyStepDone(job, step, cannedResponse(job.roadmap_md, step), nowIso())
    log(job, 'ok', `Step ${index} done - ${note}`)
    if (index < job.step_total) {
      persist(job)
      runStep(job, index + 1)
      return
    }
    job.status = 'done'
    job.finished_at = nowIso()
    const count = Object.keys(job.artifacts).length
    log(job, 'ok', `Job finished - ${job.steps_done}/${job.step_total} steps, ${count} files ready for ZIP`)
    timers.delete(job.id)
    persist(job)
  }, delayMs + jitter))
}

export function appendLog(id, level, msg) {
  const job = findJob(id)
  if (!job) return
  log(job, level, msg)
  if (!SAMPLES.includes(job)) persist(job)
}
