import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, History, RefreshCw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { listJobs } from '@/lib/api'
import { cn } from '@/lib/utils'
import JobView from './JobView'
import { FINISHED_STATUSES, STATUS_META, StatusChip, formatDateTime } from './status'

const HISTORY_LIMIT = 200
const FILTERS = ['all', ...FINISHED_STATUSES]

function HistoryList({ onOpen, refreshKey }) {
  const [jobs, setJobs] = useState(null)
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    let alive = true
    listJobs({ status: FINISHED_STATUSES, limit: HISTORY_LIMIT })
      .then((list) => { if (alive) { setJobs(list); setError(null) } })
      .catch((err) => { if (alive) { setError(err.message); setJobs((j) => j ?? []) } })
    return () => { alive = false }
  }, [attempt, refreshKey])

  const counts = useMemo(() => {
    const c = { all: jobs?.length ?? 0 }
    for (const j of jobs ?? []) c[j.status] = (c[j.status] ?? 0) + 1
    return c
  }, [jobs])

  const q = query.trim().toLowerCase()
  const shown = (jobs ?? []).filter((j) => (filter === 'all' || j.status === filter)
    && (!q || j.title.toLowerCase().includes(q) || (j.model || '').toLowerCase().includes(q) || j.id.startsWith(q)))

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-5 lg:p-7" data-testid="history-tab">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight"><History className="size-5" /> Job history</h2>
          <p className="text-[13px] text-muted-foreground">Finished jobs (up to {HISTORY_LIMIT} most recent). Open one to view files, transcript and log, or to Resume / Restart it.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setAttempt((a) => a + 1)} data-testid="history-refresh"><RefreshCw /> Refresh</Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search title, model or job id" className="h-9 bg-card pl-8" data-testid="history-search" />
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <Button
              key={f}
              size="sm"
              variant={filter === f ? 'default' : 'outline'}
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              data-testid={`history-filter-${f}`}
              className={cn(filter !== f && 'bg-card')}
            >
              {f === 'all' ? 'All' : STATUS_META[f].label}
              <span className="font-mono text-[10.5px] opacity-70">{counts[f] ?? 0}</span>
            </Button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-coral/30 bg-coral-soft p-3.5 text-[13px] text-coral" data-testid="history-error">
          Could not load jobs: <span className="text-foreground/80">{error}</span>
        </div>
      )}
      {jobs === null && [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
      {jobs && shown.length === 0 && !error && (
        <p className="rounded-xl border border-dashed border-input bg-paper p-6 text-center text-sm text-muted-foreground" data-testid="history-empty">
          {jobs.length ? 'No jobs match this search or filter.' : 'No finished jobs yet.'}
        </p>
      )}
      <ul className="space-y-2" data-testid="history-list">
        {shown.map((j) => (
          <li key={j.id}>
            <button
              type="button"
              onClick={() => onOpen(j.id)}
              data-testid={`history-job-${j.id}`}
              className="group flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-border bg-card px-3.5 py-3 text-left transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-px hover:border-input hover:shadow-md"
            >
              <StatusChip status={j.status} testId="history-job-status" />
              <div className="min-w-[200px] flex-1">
                <p className="truncate text-[13.5px] font-medium">{j.title}</p>
                <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                  <span className="font-mono">{j.model}</span>
                  <span>-</span>
                  <span>{j.steps_done}/{j.step_total} steps</span>
                  {j.failed_step && <span className="text-coral">- failed at step {j.failed_step}</span>}
                  {j.stopped_step && <span className="text-stop">- stopped at step {j.stopped_step}</span>}
                  {j.restarted_from && <span>- restart of <span className="font-mono">{j.restarted_from.slice(0, 8)}</span></span>}
                </p>
              </div>
              <span className="text-xs text-muted-foreground">{formatDateTime(j.finished_at || j.created_at)}</span>
              <ArrowRight className="size-4 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Job history tab: searchable list of finished jobs; an opened job (?job=) uses the full job view.
export default function HistoryTab({ jobId, onOpen, onClose, onOpenJob, onOpenQueue, onQueueChanged, refreshKey }) {
  if (jobId) {
    return (
      <JobView
        jobId={jobId}
        onBack={onClose}
        backLabel="Back to job history"
        onOpenJob={onOpenJob}
        onOpenQueue={onOpenQueue}
        onQueueChanged={onQueueChanged}
      />
    )
  }
  return <HistoryList onOpen={onOpen} refreshKey={refreshKey} />
}
