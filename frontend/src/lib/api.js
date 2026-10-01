// Data access layer for the ROADMAP2ARENA frontend.
//
// Every function here is async and returns plain JSON-like objects so the mock
// implementation can later be swapped for real fetch() calls against
// VITE_BACKEND_URL (e.g. GET /api/config, POST /api/jobs, GET /api/jobs/:id,
// GET /api/jobs/:id/zip) without touching components.

import { DEFAULT_CONFIG } from '@/mock'
import { parseRoadmap as parseLocal } from './roadmapParser'
import { appendLog, createJob, findJob, getAllJobs } from './mockEngine'
import { buildZip, triggerDownload } from './zip'

const clone = (v) => structuredClone(v)
const sortedArtifacts = (map) => Object.values(map).sort((a, b) => a.path.localeCompare(b.path))

function toJobDto(job) {
  const dto = clone(job)
  dto.artifacts = sortedArtifacts(job.artifacts)
  return dto
}

function roadmapTitle(job) {
  const h1 = job.roadmap_md.match(/^#\s+(.+)$/m)
  return h1 ? h1[1].trim() : job.steps[0]?.title || 'Untitled roadmap'
}

export async function getConfig() {
  return clone(DEFAULT_CONFIG)
}

export async function parseRoadmap(markdown) {
  return { steps: parseLocal(markdown) }
}

export async function startJob({ arena_url, model, step_delay_seconds, project_context, roadmap_md }) {
  const job = createJob({
    config: { arena_url, model, step_delay_seconds: step_delay_seconds ?? DEFAULT_CONFIG.step_delay_seconds },
    project_context,
    roadmap_md,
  })
  return { id: job.id }
}

export async function getJob(id) {
  const job = findJob(id)
  if (!job) throw new Error(`Job ${id} not found`)
  return toJobDto(job)
}

export async function listJobs() {
  return getAllJobs().map((j) => ({
    id: j.id,
    created_at: j.created_at,
    finished_at: j.finished_at,
    status: j.status,
    model: j.config.model,
    title: roadmapTitle(j),
    is_sample: j.id.startsWith('job-sample-'),
    steps_done: j.steps_done,
    step_total: j.step_total,
    failed_step: j.failed_step,
  }))
}

export async function getStep(jobId, index) {
  const job = findJob(jobId)
  const step = job?.steps[index - 1]
  if (!step) throw new Error(`Step ${index} of job ${jobId} not found`)
  return clone(step)
}

// Builds the ZIP in the browser and starts the download.
// Returns { filename, included, skipped } so the UI can report what happened.
export async function downloadZip(jobId) {
  const job = findJob(jobId)
  if (!job) throw new Error(`Job ${jobId} not found`)
  if (!job.steps_done) throw new Error('No completed steps yet')
  const { blob, included, skipped } = await buildZip(sortedArtifacts(job.artifacts))
  for (const s of skipped) appendLog(jobId, 'warn', `ZIP: skipped "${s.path}" - ${s.reason}`)
  appendLog(jobId, 'ok', `ZIP: packed ${included.length} files`)
  const filename = `roadmap2arena-${jobId}.zip`
  triggerDownload(blob, filename)
  return { filename, included, skipped }
}
