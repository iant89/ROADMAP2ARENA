import { Download, FlaskConical, History, Route } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { MOCK_MODE_NOTICE } from '@/mock'
import { StatusChip } from './status'

export default function HeaderBar({ job, onDownload, downloading, onOpenRecent }) {
  const status = job ? job.status : 'idle'
  const done = job?.steps_done ?? 0
  const total = job?.step_total ?? 0
  const pct = total ? Math.round((done / total) * 100) : 0
  const canDownload = Boolean(job && done >= 1)
  const emphasize = job?.status === 'done'

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

        <StatusChip status={status} />

        <div className="flex min-w-[180px] flex-1 items-center gap-3" data-testid="job-progress">
          <Progress
            value={pct}
            className={cn('h-2 bg-secondary', status === 'error' && '[&>div]:bg-coral', status === 'done' && '[&>div]:bg-teal')}
          />
          <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
            {done} / {total} steps
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="hidden items-center gap-1.5 rounded-full border border-amber/40 bg-amber-soft px-2.5 py-1 text-xs font-medium text-amber md:inline-flex" data-testid="mock-mode-badge">
                <FlaskConical className="size-3.5" /> Mock mode
              </span>
            </TooltipTrigger>
            <TooltipContent>{MOCK_MODE_NOTICE}</TooltipContent>
          </Tooltip>
          <Button variant="outline" size="lg" onClick={onOpenRecent} data-testid="recent-jobs-button" className="px-3 hover:-translate-y-px">
            <History /> Recent jobs
          </Button>
          <Button
            size="lg"
            onClick={onDownload}
            disabled={!canDownload || downloading}
            data-testid="download-zip-button"
            className={cn(
              'px-3 hover:-translate-y-px',
              emphasize && 'r2a-pulse bg-teal text-white hover:bg-teal/90',
            )}
          >
            <Download /> {downloading ? 'Packing...' : 'Download ZIP'}
          </Button>
        </div>
      </div>
      <div className="flex items-center gap-2 border-t border-amber/25 bg-amber-soft px-5 py-1.5 text-xs font-medium text-amber lg:px-8" data-testid="mock-mode-banner">
        <FlaskConical className="size-3.5 shrink-0" />
        {MOCK_MODE_NOTICE}
      </div>
    </header>
  )
}
