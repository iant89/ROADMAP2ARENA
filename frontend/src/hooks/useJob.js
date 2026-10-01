import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { getJob } from '@/lib/api'

const POLL_MS = 400

// Loads a job by id and polls it while it is running. Fires toasts when a job
// observed as running in this session finishes or fails.
export function useJob(jobId) {
  const [job, setJob] = useState(null)
  const lastStatus = useRef({})

  const refresh = useCallback(async () => {
    if (!jobId) return null
    try {
      const next = await getJob(jobId)
      const prev = lastStatus.current[jobId]
      if (prev === 'running' && next.status === 'done') {
        toast.success('Job finished', { description: `${next.steps_done}/${next.step_total} steps done - ${next.artifacts.length} files ready for ZIP` })
      } else if (prev === 'running' && next.status === 'error') {
        toast.error(`Step ${next.failed_step ?? '?'} failed`, { description: next.error })
      }
      lastStatus.current[jobId] = next.status
      setJob(next)
      return next
    } catch (err) {
      toast.error('Could not load job', { description: err.message })
      return null
    }
  }, [jobId])

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return undefined
    }
    let timer
    let alive = true
    const tick = async () => {
      const next = await refresh()
      if (alive && next?.status === 'running') timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [jobId, refresh])

  return { job, refresh }
}
