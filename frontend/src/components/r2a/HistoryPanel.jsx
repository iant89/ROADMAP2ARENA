import { useEffect, useMemo, useState } from 'react'
import { History, PanelLeftClose, PanelLeftOpen, RefreshCw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { listJobs } from '@/lib/api'
import { cn } from '@/lib/utils'
import { STATUS_META, StatusChip, StatusIcon, formatDateTime } from './status'

const STORAGE_KEY = 'r2a.historyPanel.collapsed'
const LIMIT = 200
const POLL_MS = 4000
const FILTERS = ['all', 'queued', 'paused', 'running', 'done', 'error', 'stopped', 'cancelled']

function readCollapsed() {
  try { return window.localStorage.getItem(STORAGE_KEY) === '1' } catch { return false }
}

// Jobs list (all statuses, most recent first), polled so badges stay current.
function useJobList(refreshKey) {
  const [jobs, setJobs] = useState(null)
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    let timer
    const tick = async () => {
      try {
        const list = await listJobs({ limit: LIMIT })
        if (alive) { setJobs(list); setError(null) }
      } catch (err) {
        if (alive) { setError(err.message); setJobs((j) => j ?? []) }
      }
      if (alive) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, [refreshKey, attempt])
  return { jobs, error, reload: () => setAttempt((a) => a + 1) }
}

function Rail({ jobs, selectedId, onSelect, onExpand }) {
  return (
    <aside className="flex h-full w-14 shrink-0 flex-col items-center border-r border-border bg-paper py-3" data-testid="history-panel-rail">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button size="icon-sm" variant="ghost" onClick={onExpand} aria-label="Expand job list" data-testid="history-panel-expand"><PanelLeftOpen /></Button>
        </TooltipTrigger>
        <TooltipContent side="right">Expand job list</TooltipContent>
      </Tooltip>
      <History className="mt-3 mb-2 size-4 text-muted-foreground" />
      <ScrollArea className="min-h-0 w-full flex-1">
        <ul className="flex flex-col items-center gap-1 pb-2">
          {(jobs ?? []).map((j) => (
            <li key={j.id}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => onSelect(j.id)}
                    aria-label={`${j.title} (${STATUS_META[j.status]?.label ?? j.status})`}
                    data-testid={`history-rail-job-${j.id}`}
                    className={cn('grid size-9 place-items-center rounded-lg hover:bg-muted', j.id === selectedId && 'bg-card ring-2 ring-primary')}
                  >
                    <StatusIcon status={j.status} />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="right">{j.title} - {STATUS_META[j.status]?.label ?? j.status}</TooltipContent>
              </Tooltip>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </aside>
  )
}

// Collapsible side panel of previous jobs. Collapsed = narrow rail of status icons;
// the state is remembered in localStorage.
export default function HistoryPanel({ selectedId, onSelect, refreshKey, isDesktop }) {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const { jobs, error, reload } = useJobList(refreshKey)

  const setAndStore = (value) => {
    setCollapsed(value)
    try { window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* private mode */ }
  }

  const counts = useMemo(() => {
    const c = { all: jobs?.length ?? 0 }
    for (const j of jobs ?? []) c[j.status] = (c[j.status] ?? 0) + 1
    return c
  }, [jobs])
  const q = query.trim().toLowerCase()
  const shown = (jobs ?? []).filter((j) => (filter === 'all' || j.status === filter)
    && (!q || j.title.toLowerCase().includes(q) || (j.model || '').toLowerCase().includes(q) || j.id.startsWith(q)))

  if (collapsed && isDesktop) return <Rail jobs={jobs} selectedId={selectedId} onSelect={onSelect} onExpand={() => setAndStore(false)} />

  return (
    <aside
      className={cn('flex shrink-0 flex-col border-border bg-paper', isDesktop ? 'h-full w-[340px] border-r' : 'w-full border-b')}
      data-testid="history-panel"
    >
      <div className="flex items-center gap-2 px-4 pt-4 pb-2">
        <History className="size-4" />
        <h2 className="flex-1 text-[15px] font-semibold tracking-tight">Job history</h2>
        <Button size="icon-sm" variant="ghost" onClick={reload} aria-label="Refresh job list" data-testid="history-refresh"><RefreshCw /></Button>
        <Button size="icon-sm" variant="ghost" onClick={() => setAndStore(!collapsed)} aria-label={collapsed ? 'Expand job list' : 'Collapse job list'} data-testid="history-panel-collapse">
          {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
        </Button>
      </div>
      {!(collapsed && !isDesktop) && (
        <>
          <div className="space-y-2.5 px-4 pb-3">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, model or id" className="h-8 bg-card pl-8 text-[13px]" data-testid="history-search" />
            </div>
            <div className="flex flex-nowrap gap-1.5 overflow-x-auto pb-1" role="group" aria-label="Filter by status" data-testid="history-filters">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  aria-pressed={filter === f}
                  data-testid={`history-filter-${f}`}
                  className={cn('shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap transition-colors',
                    filter === f ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground hover:text-foreground')}
                >
                  {f === 'all' ? 'All' : STATUS_META[f].label} <span className="font-mono opacity-70">{counts[f] ?? 0}</span>
                </button>
              ))}
            </div>
          </div>
          <ListScroll isDesktop={isDesktop}>
            <ul className="space-y-1.5 p-2.5" data-testid="history-list">
              {jobs === null && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
              {error && <li className="rounded-lg border border-coral/30 bg-coral-soft p-3 text-[12.5px] text-coral" data-testid="history-error">Could not load jobs: {error}</li>}
              {jobs && !shown.length && !error && (
                <li className="p-4 text-center text-[13px] text-muted-foreground" data-testid="history-empty">{jobs.length ? 'No jobs match.' : 'No jobs yet.'}</li>
              )}
              {shown.map((j) => (
                <li key={j.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(j.id)}
                    data-testid={`history-job-${j.id}`}
                    aria-current={j.id === selectedId ? 'true' : undefined}
                    className={cn('w-full rounded-lg border px-3 py-2.5 text-left transition-[border-color,box-shadow] duration-150 hover:border-input hover:shadow-sm',
                      j.id === selectedId ? 'border-primary bg-card ring-1 ring-primary' : 'border-transparent bg-card/70')}
                  >
                    <div className="flex items-center gap-2">
                      <StatusChip status={j.status} testId="history-job-status" className="h-5 px-2 text-[10px]" />
                      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{formatDateTime(j.created_at)}</span>
                    </div>
                    <p className="mt-1.5 truncate text-[13px] font-medium">{j.title}</p>
                    <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
                      <span className="font-mono">{j.model}</span> - {j.steps_done}/{j.step_total} steps
                      {j.queue_position ? ` - #${j.queue_position} in queue` : ''}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </ListScroll>
        </>
      )}
    </aside>
  )
}

// Desktop: Radix scroll area filling the panel height. Mobile: the list is the whole page (the detail
// replaces it when a job is open), so it simply flows with the page scroll.
function ListScroll({ isDesktop, children }) {
  if (isDesktop) return <ScrollArea className="min-h-0 flex-1 border-t border-border">{children}</ScrollArea>
  return <div className="border-t border-border" data-testid="history-list-scroll">{children}</div>
}
