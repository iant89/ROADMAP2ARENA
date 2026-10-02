import { useCallback, useEffect, useRef, useState } from 'react'
import { getQueue } from '@/lib/api'

const POLL_MS = 2000

// Polls GET /api/queue (running job + ordered queued/paused jobs) every 2 s.
export function useQueue() {
  const [queue, setQueue] = useState(null)
  const [error, setError] = useState(null)
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const next = await getQueue()
      if (alive.current) { setQueue(next); setError(null) }
      return next
    } catch (err) {
      if (alive.current) setError(err.message)
      return null
    }
  }, [])

  useEffect(() => {
    alive.current = true
    let timer
    const tick = async () => {
      await refresh()
      if (alive.current) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => { alive.current = false; clearTimeout(timer) }
  }, [refresh])

  return { queue, error, refresh }
}
