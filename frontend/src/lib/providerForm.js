// Pure helpers for the provider + model fields (Create job, Clone, Resume/Restart overrides).

export const LEGACY = '__legacy__'

export function isHttpUrl(value) {
  try {
    const u = new URL(String(value).trim())
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.host)
  } catch {
    return false
  }
}

// The select value for a { provider_id, arena_url } form: a provider id, LEGACY (a provider-less
// job that keeps its own arena2api URL, only offered when cloning/resuming such a job) or ''.
export const selectionOf = (v) => v.provider_id || (v.arena_url?.trim() ? LEGACY : '')

export function providerError(v, providers) {
  const sel = selectionOf(v)
  if (sel === LEGACY) return isHttpUrl(v.arena_url) ? null : 'Use an http:// or https:// URL'
  if (!sel) return providers?.length ? 'Choose a provider' : 'Add a provider in Settings first'
  if (providers && !providers.some((p) => p.id === sel)) return 'This provider was deleted - choose another one'
  return null
}

export const modelError = (v) => (v.model.trim() ? null : 'Model is required')
