import { ListOrdered, Route } from 'lucide-react'
import { Progress } from '@/components/ui/progress'
import { StatusChip } from './status'

// Status badge + progress of the running job (from GET /api/queue), plus the queue size.
export default function HeaderBar({ running, queueCount, onOpenCurrent, onOpenQueue }) {
  const done = running?.steps_done ?? 0
  const total = running?.step_total ?? 0
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <header className="border-b border-border bg-card">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3.5 lg:px-8">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Route className="size-5" />
          </span>
          <div className="leading-tight">
            <h1 className="text-[15px] font-bold tracking-[0.08em]">ROADMAP2ARENA</h1>
            <p className="text-xs text-muted-foreground">ROADMAP.md to files, one step at a time</p>
          </div>
        </div>

        <button type="button" onClick={onOpenCurrent} className="rounded-full" aria-label="Open current job">
          <StatusChip status={running ? 'running' : 'idle'} />
        </button>

        <div className="flex min-w-[180px] flex-1 items-center gap-3" data-testid="job-progress">
          {running && <span className="hidden max-w-[280px] shrink-0 truncate text-[13px] font-medium md:inline" data-testid="header-job-title">{running.title}</span>}
          <Progress value={pct} className="h-2 bg-secondary" />
          <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
            {done} / {total} steps
          </span>
        </div>

        <button
          type="button"
          onClick={onOpenQueue}
          data-testid="header-queue-count"
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ListOrdered className="size-4" /> {queueCount} queued
        </button>
      </div>
    </header>
  )
}
