import { useCallback, useEffect, useState } from 'react'

export const TABS = ['create', 'queue', 'current', 'history', 'projects', 'settings']

// ?tab=<tab>&job=<id>. A bare ?job=<id> (old deep links) opens the job in history.
function read() {
  const params = new URLSearchParams(window.location.search)
  const job = params.get('job') || null
  let tab = params.get('tab')
  if (!TABS.includes(tab)) tab = job ? 'history' : 'create'
  return { tab, job: tab === 'history' ? job : null }
}

function write({ tab, job }, replace) {
  const params = new URLSearchParams()
  params.set('tab', tab)
  if (job && tab === 'history') params.set('job', job)
  const url = `${window.location.pathname}?${params}`
  if (url === `${window.location.pathname}${window.location.search}`) return
  window.history[replace ? 'replaceState' : 'pushState'](null, '', url)
}

// Selected top-level tab and opened history job, kept in the URL so reload keeps place.
export function useUrlState() {
  const [state, setState] = useState(read)

  useEffect(() => {
    write(state, true) // normalise the URL on first load
    const onPop = () => setState(read())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const navigate = useCallback((tab, job = null) => {
    const next = { tab, job: tab === 'history' ? job : null }
    write(next, false)
    setState(next)
  }, [])

  return [state, navigate]
}
