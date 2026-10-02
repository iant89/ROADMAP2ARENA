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
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2.5 px-5 py-3 sm:gap-y-3 sm:py-3.5 lg:px-8">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Route className="size-5" />
          </span>
          <div className="leading-tight">
            <h1 className="text-[15px] font-bold tracking-[0.08em]">ROADMAP2ARENA</h1>
            <p className="hidden text-xs text-muted-foreground sm:block">ROADMAP.md to files, one step at a time</p>
          </div>
        </div>

        {/* Status row: chip, running job name + progress, queue size. On mobile it is one full-width row. */}
        <div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-0 sm:gap-6" data-testid="header-status-row">
          <button type="button" onClick={onOpenCurrent} className="shrink-0 rounded-full" aria-label="Open current job">
            <StatusChip status={running ? 'running' : 'idle'} />
          </button>

          <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3" data-testid="job-progress">
            {running && <span className="hidden max-w-[16rem] shrink-0 truncate text-[13px] font-medium md:inline" title={running.title} data-testid="header-job-title">{running.title}</span>}
            <Progress value={pct} className="h-2 min-w-[8rem] flex-1 bg-secondary" />
            <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums" data-testid="header-step-count">
              {done} / {total}<span className="hidden sm:inline"> steps</span>
            </span>
          </div>

          <button
            type="button"
            onClick={onOpenQueue}
            data-testid="header-queue-count"
            aria-label={`${queueCount} queued - open job queue`}
            className="flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground sm:px-2"
          >
            <ListOrdered className="size-4" /> {queueCount}<span className="hidden sm:inline">queued</span>
          </button>
        </div>
      </div>
    </header>
  )
}
