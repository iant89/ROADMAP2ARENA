import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Activity, ArrowDown, ArrowRight, ArrowUp, ListOrdered, Pause, Play, PlusCircle, Undo2, XCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cancelQueued, moveQueued, pauseQueued, unpauseQueued } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatusChip, providerName, relativeTime } from './status'

const CONFIRM_MS = 5000

function IconAction({ label, onClick, disabled, testId, children, className }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="icon-sm" variant="ghost" aria-label={label} onClick={onClick} disabled={disabled} data-testid={testId} className={className}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function RunningCard({ job, onOpen }) {
  const pct = job.step_total ? Math.round((job.steps_done / job.step_total) * 100) : 0
  return (
    <div className="rounded-xl border border-amber/30 bg-card p-4 shadow-sm" data-testid="queue-running">
      <div className="flex flex-wrap items-center gap-3">
        <StatusChip status="running" testId="queue-running-status" />
        <p className="min-w-[160px] flex-1 truncate text-[14px] font-semibold" data-testid="queue-running-title">{job.title || 'Untitled roadmap'}</p>
        <Button size="sm" variant="outline" onClick={onOpen} data-testid="queue-open-current"><Activity /> Open current job</Button>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Progress value={pct} className="h-1.5 bg-secondary" />
        <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{job.steps_done}/{job.step_total} steps</span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        <span className="font-mono">{job.model}</span> via <span className="font-medium" data-testid="queue-running-provider">{providerName(job.provider)}</span> - started {relativeTime(job.started_at || job.created_at)}
      </p>
    </div>
  )
}

function QueueRow({ job, first, last, busy, onAction, onOpen }) {
  const [confirm, setConfirm] = useState(false)
  useEffect(() => {
    if (!confirm) return undefined
    const t = setTimeout(() => setConfirm(false), CONFIRM_MS)
    return () => clearTimeout(t)
  }, [confirm])
  const paused = job.status === 'paused'

  return (
    <li
      className={cn('r2a-rise flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-3.5 py-3', paused ? 'border-dashed border-pause/40 bg-pause-soft/40' : 'border-border')}
      data-testid={`queue-row-${job.job_id}`}
    >
      <span className={cn('grid size-8 shrink-0 place-items-center rounded-md font-mono text-sm font-semibold', paused ? 'bg-pause-soft text-pause' : 'bg-queue-soft text-queue')} data-testid="queue-position">
        {job.queue_position}
      </span>
      <button type="button" onClick={onOpen} className="group min-w-[200px] flex-1 text-left" data-testid="queue-open-job">
        <p className="truncate text-[13.5px] font-medium group-hover:underline">{job.title || 'Untitled roadmap'}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span className="font-mono">{job.model}</span>
          <span>via <span className="font-medium" data-testid="queue-job-provider">{providerName(job.provider)}</span></span>
          <span>-</span>
          <span>{job.step_total} step{job.step_total === 1 ? '' : 's'}</span>
          <span>-</span>
          <span title={job.queued_at}>added {relativeTime(job.queued_at || job.created_at)}</span>
          {job.steps_done > 0 && <span>- resumes after {job.steps_done} done</span>}
        </p>
      </button>
      <StatusChip status={job.status} testId="queue-row-status" />
      {confirm ? (
        <div className="flex items-center gap-1.5" data-testid="queue-remove-confirm">
          <span className="text-xs font-medium text-coral">Cancel this job? It stays in history.</span>
          <Button size="sm" variant="outline" onClick={() => { setConfirm(false); onAction('remove') }} disabled={busy} data-testid="queue-remove-yes" className="border-coral/40 text-coral hover:bg-coral-soft hover:text-coral"><XCircle /> Cancel job</Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)} data-testid="queue-remove-no"><Undo2 /> Keep</Button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5">
          <IconAction label="Move up" onClick={() => onAction('up')} disabled={busy || first} testId="queue-move-up"><ArrowUp /></IconAction>
          <IconAction label="Move down" onClick={() => onAction('down')} disabled={busy || last} testId="queue-move-down"><ArrowDown /></IconAction>
          {paused ? (
            <IconAction label="Unpause" onClick={() => onAction('unpause')} disabled={busy} testId="queue-unpause" className="text-teal hover:text-teal"><Play /></IconAction>
          ) : (
            <IconAction label="Pause (moves to the end)" onClick={() => onAction('pause')} disabled={busy} testId="queue-pause" className="text-pause hover:text-pause"><Pause /></IconAction>
          )}
          <IconAction label="Cancel (keeps it in Job history)" onClick={() => setConfirm(true)} disabled={busy} testId="queue-remove" className="text-coral hover:text-coral"><XCircle /></IconAction>
        </div>
      )}
    </li>
  )
}

const ACTIONS = {
  up: { fn: (id) => moveQueued(id, 'up') },
  down: { fn: (id) => moveQueued(id, 'down') },
  pause: { fn: pauseQueued, msg: 'Paused - moved to the end of the queue' },
  unpause: { fn: unpauseQueued, msg: 'Unpaused - eligible to run again' },
  remove: { fn: cancelQueued, msg: 'Cancelled - removed from the queue, kept in Job history' },
}

// Job queue tab: running job pinned on top, then queued/paused jobs in order.
export default function QueueTab({ queue, error, refresh, onOpenCurrent, onOpenJob, onCreate }) {
  const [busyId, setBusyId] = useState(null)

  const run = async (job, action) => {
    setBusyId(job.job_id)
    try {
      await ACTIONS[action].fn(job.job_id)
      if (ACTIONS[action].msg) toast(ACTIONS[action].msg, { description: job.title })
    } catch (err) {
      toast.error('Queue update failed', { description: err.message })
    } finally {
      await refresh()
      setBusyId(null)
    }
  }

  if (!queue) {
    return (
      <div className="mx-auto max-w-4xl space-y-3 p-5 lg:p-7">
        {error ? <p className="text-sm text-coral">Could not load the queue: {error}</p> : [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
      </div>
    )
  }
  const { running, queued } = queue
  const paused = queued.filter((j) => j.status === 'paused').length

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7" data-testid="queue-tab">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight"><ListOrdered className="size-5" /> Job queue</h2>
          <p className="text-[13px] text-muted-foreground" data-testid="queue-summary">
            {queued.length ? `${queued.length} waiting${paused ? ` (${paused} paused)` : ''}. ` : 'No jobs waiting. '}
            One job runs at a time; the next unpaused job starts automatically.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={onCreate} data-testid="queue-create-button"><PlusCircle /> Add a job</Button>
      </div>

      {running ? (
        <RunningCard job={running} onOpen={onOpenCurrent} />
      ) : (
        <div className="rounded-xl border border-dashed border-input bg-paper p-4 text-[13px] text-muted-foreground" data-testid="queue-idle">
          Nothing is running{queued.length && !queue.waiting ? ' - every waiting job is paused. Unpause one to start it.' : '.'}
        </div>
      )}

      {queued.length > 0 ? (
        <ol className="space-y-2" data-testid="queue-list">
          {queued.map((job, i) => (
            <QueueRow
              key={job.job_id}
              job={job}
              first={i === 0}
              last={i === queued.length - 1}
              busy={busyId !== null}
              onAction={(action) => run(job, action)}
              onOpen={() => onOpenJob(job.job_id)}
            />
          ))}
        </ol>
      ) : (
        <div className="grid place-items-center rounded-xl border border-dashed border-input bg-paper px-6 py-12 text-center" data-testid="queue-empty">
          <ListOrdered className="size-7 text-muted-foreground" />
          <p className="mt-3 text-sm font-semibold">The queue is empty</p>
          <p className="mt-1 max-w-sm text-[13px] text-muted-foreground">Jobs added while another one runs wait here. Reorder, pause or cancel them.</p>
          <Button size="sm" className="mt-4" onClick={onCreate}><PlusCircle /> Create a job <ArrowRight /></Button>
        </div>
      )}
    </div>
  )
}
