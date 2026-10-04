import { useEffect, useSyncExternalStore } from 'react'
import { fetchProviderModels, getProviders } from '@/lib/api'

// Shared provider list: Settings edits it, the Create/Clone forms and job controls read it.
// One module-level store so a change in Settings shows up everywhere without prop drilling.
let state = { data: null, error: null }
let inflight = null
const subscribers = new Set()

function emit(next) {
  state = next
  subscribers.forEach((fn) => fn())
}

export function refreshProviders() {
  if (!inflight) {
    inflight = getProviders()
      .then((data) => emit({ data, error: null }))
      .catch((err) => emit({ ...state, error: err.message }))
      .finally(() => { inflight = null })
  }
  return inflight
}

const subscribe = (fn) => { subscribers.add(fn); return () => { subscribers.delete(fn) } }
const getSnapshot = () => state

export function useProviders() {
  const snap = useSyncExternalStore(subscribe, getSnapshot)
  useEffect(() => {
    if (!state.data && !inflight) refreshProviders()
  }, [])
  return { ...snap, refresh: refreshProviders }
}

// Per-provider model lists, cached for the session (cleared when a provider is edited).
const modelCache = new Map()

export function forgetModels(id) {
  if (id) modelCache.delete(id)
  else modelCache.clear()
}

export function cachedModels(id) {
  return modelCache.get(id) ?? null
}

export async function loadModels(id, { force = false } = {}) {
  if (!force && modelCache.has(id)) return modelCache.get(id)
  const models = await fetchProviderModels(id)
  modelCache.set(id, models)
  return models
}
