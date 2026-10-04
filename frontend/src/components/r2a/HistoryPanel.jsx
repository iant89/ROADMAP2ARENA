import { useEffect, useMemo, useState } from 'react'
import { Check, CheckSquare, History, Layers, ListChecks, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { listJobs } from '@/lib/api'
import { cn } from '@/lib/utils'
import HistoryBulkBar from './HistoryBulkBar'
import { STATUS_META, StatusChip, StatusIcon, formatDateTime, providerName } from './status'

const STORAGE_KEY = 'r2a.historyPanel.collapsed'
const LIMIT = 200
const POLL_MS = 4000
const FILTERS = ['all', 'queued', 'paused', 'running', 'done', 'error', 'stopped', 'cancelled']
const FINISHED = ['done', 'error', 'stopped', 'cancelled']
// Status filter pills are icon + count only and wrap instead of scrolling; icons and colours are the
// job status icons used everywhere else (STATUS_META), "All" gets a neutral stack icon.
const FILTER_META = {
  all: { label: 'All', icon: Layers, tone: 'text-foreground' },
  ...Object.fromEntries(FILTERS.slice(1).map((f) => [f, STATUS_META[f]])),
}

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
// Selection mode (Select button) adds checkboxes and a toolbar for bulk / "all finished" deletes.
export default function HistoryPanel({ selectedId, onSelect, refreshKey, isDesktop, onDeleted }) {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [selecting, setSelecting] = useState(false)
  const [picked, setPicked] = useState(() => new Set())
  const { jobs, error, reload } = useJobList(refreshKey)

  const toggleSelecting = () => { setSelecting((v) => !v); setPicked(new Set()) }
  const togglePick = (j) => {
    if (j.status === 'running') return
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(j.id)) next.delete(j.id)
      else next.add(j.id)
      return next
    })
  }
  const handleDeleted = (ids) => { reload(); onDeleted?.(ids) }

  const setAndStore = (value) => {
    setCollapsed(value)
    try { window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* private mode */ }
  }

  const counts = useMemo(() => {
    const c = { all: jobs?.length ?? 0 }
    for (const j of jobs ?? []) c[j.status] = (c[j.status] ?? 0) + 1
    return c
  }, [jobs])
  const finishedCount = FINISHED.reduce((n, st) => n + (counts[st] ?? 0), 0)
  // drop picks that vanished from the list or started running
  const live = new Set((jobs ?? []).filter((j) => j.status !== 'running').map((j) => j.id))
  const selected = [...picked].every((id) => live.has(id)) ? picked : new Set([...picked].filter((id) => live.has(id)))
  const q = query.trim().toLowerCase()
  const shown = (jobs ?? []).filter((j) => (filter === 'all' || j.status === filter)
    && (!q || j.title.toLowerCase().includes(q) || (j.model || '').toLowerCase().includes(q) || providerName(j.provider).toLowerCase().includes(q) || j.id.startsWith(q)))

  if (collapsed && isDesktop) return <Rail jobs={jobs} selectedId={selectedId} onSelect={onSelect} onExpand={() => setAndStore(false)} />

  return (
    <aside
      className={cn('flex shrink-0 flex-col border-border bg-paper', isDesktop ? 'h-full w-[340px] border-r' : 'w-full border-b')}
      data-testid="history-panel"
    >
      <div className="flex items-center gap-2 px-4 pt-4 pb-2">
        <History className="size-4" />
        <h2 className="flex-1 text-[15px] font-semibold tracking-tight">Job history</h2>
        {!(collapsed && !isDesktop) && (
          <Button size="xs" variant={selecting ? 'default' : 'ghost'} onClick={toggleSelecting} aria-pressed={selecting} data-testid="history-select-toggle">
            {selecting ? <><Check /> Done</> : <><ListChecks /> Select</>}
          </Button>
        )}
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
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, model, provider or id" className="h-8 bg-card pl-8 text-[13px]" data-testid="history-search" />
            </div>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status" data-testid="history-filters">
              {FILTERS.map((f) => {
                const meta = FILTER_META[f]
                const Icon = meta.icon
                const n = counts[f] ?? 0
                const active = filter === f
                const label = `${meta.label}: ${n}`
                return (
                  <Tooltip key={f}>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={() => setFilter(f)}
                        aria-pressed={active}
                        aria-label={label}
                        title={label}
                        data-testid={`history-filter-${f}`}
                        className={cn('inline-flex h-7 max-w-[4.5rem] flex-auto items-center justify-center gap-0.5 rounded-full border px-1 text-[11px] font-medium transition-colors outline-none',
                          'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-paper',
                          active ? 'border-foreground bg-card text-foreground shadow-sm ring-1 ring-foreground' : 'border-border bg-card text-muted-foreground hover:border-foreground/40 hover:text-foreground',
                          !active && n === 0 && 'opacity-60')}
                      >
                        <Icon aria-hidden="true" className={cn('size-3.5 shrink-0', meta.tone)} />
                        <span className="font-mono tabular-nums">{n}</span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">{label}</TooltipContent>
                  </Tooltip>
                )
              })}
            </div>
            {selecting && (
              <HistoryBulkBar visible={shown} selected={selected} setSelected={setPicked} finishedCount={finishedCount} onDeleted={handleDeleted} />
            )}
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
                    onClick={() => (selecting ? togglePick(j) : onSelect(j.id))}
                    data-testid={`history-job-${j.id}`}
                    aria-current={!selecting && j.id === selectedId ? 'true' : undefined}
                    aria-pressed={selecting ? selected.has(j.id) : undefined}
                    aria-disabled={selecting && j.status === 'running' ? 'true' : undefined}
                    title={selecting && j.status === 'running' ? "Running jobs can't be deleted - stop it first" : undefined}
                    className={cn('w-full rounded-lg border px-3 py-2.5 text-left transition-[border-color,box-shadow] duration-150 hover:border-input hover:shadow-sm',
                      selecting && selected.has(j.id) ? 'border-coral/60 bg-coral-soft/60'
                        : !selecting && j.id === selectedId ? 'border-primary bg-card ring-1 ring-primary' : 'border-transparent bg-card/70',
                      selecting && j.status === 'running' && 'cursor-not-allowed opacity-60')}
                  >
                    <div className="flex items-center gap-2">
                      {selecting && (selected.has(j.id)
                        ? <CheckSquare className="size-4 shrink-0 text-coral" data-testid="history-job-checked" />
                        : <Square className="size-4 shrink-0 text-muted-foreground" />)}
                      <StatusChip status={j.status} testId="history-job-status" className="h-5 px-2 text-[10px]" />
                      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{formatDateTime(j.created_at)}</span>
                    </div>
                    <p className="mt-1.5 truncate text-[13px] font-medium">{j.title}</p>
                    <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
                      <span className="font-mono">{j.model}</span> via {providerName(j.provider)} - {j.steps_done}/{j.step_total} steps
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
