import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { getJob } from '@/lib/api'

const POLL_MS = 1500
const LIVE = ['running', 'queued', 'paused']

// Loads a job by id and polls GET /api/jobs/{id} every 1.5 s while it is running,
// queued or paused (a queued job starts on its own when its turn comes).
// Transient errors keep the last known state and retry on the next tick.
// Finish/fail toasts are global (App, from the queue poll), not per view.
export function useJob(jobId) {
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [pollKey, setPollKey] = useState(0)
  const lastStatus = useRef({})
  const errorToastShown = useRef(false)

  const refresh = useCallback(async () => {
    if (!jobId) return null
    try {
      const next = await getJob(jobId)
      lastStatus.current[jobId] = next.status
      errorToastShown.current = false
      setError(null)
      setJob(next)
      return next
    } catch (err) {
      setError(err.message)
      if (!errorToastShown.current) {
        errorToastShown.current = true
        toast.error('Lost contact with the backend', { description: `${err.message} - retrying automatically` })
      }
      if (err.status === 404) return { status: 'missing' }
      return { status: lastStatus.current[jobId] || 'running' }
    }
  }, [jobId])

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      setError(null)
      return undefined
    }
    let timer
    let alive = true
    const tick = async () => {
      const next = await refresh()
      if (alive && LIVE.includes(next?.status)) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [jobId, refresh, pollKey])

  // Call after an action that sets the job running again (e.g. resume).
  const restartPolling = useCallback(() => setPollKey((k) => k + 1), [])

  return { job, error, refresh, restartPolling }
}
