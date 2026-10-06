import { useEffect, useSyncExternalStore } from 'react'
import { listProjects } from '@/lib/api'

// Shared project list: the Projects screen edits it while Create/Clone forms consume it.
let state = { data: null, error: null }
let inflight = null
const subscribers = new Set()

function emit(next) {
  state = next
  subscribers.forEach((fn) => fn())
}

export function refreshProjects() {
  if (!inflight) {
    inflight = listProjects()
      .then((data) => emit({ data, error: null }))
      .catch((err) => emit({ ...state, error: err.message }))
      .finally(() => { inflight = null })
  }
  return inflight
}

const subscribe = (fn) => { subscribers.add(fn); return () => subscribers.delete(fn) }
const getSnapshot = () => state

export function useProjects() {
  const snap = useSyncExternalStore(subscribe, getSnapshot)
  useEffect(() => {
    if (!state.data && !inflight) refreshProjects()
  }, [])
  return { ...snap, refresh: refreshProjects }
}
