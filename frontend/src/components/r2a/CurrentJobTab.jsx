import { useEffect, useState } from 'react'
import { Activity, ArrowRight, ListOrdered, PlusCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { listJobs } from '@/lib/api'
import JobView from './JobView'
import { StatusChip, formatDateTime } from './status'

function EmptyState({ queue, onOpenJob, onOpenQueue, onCreate }) {
  const [recent, setRecent] = useState(undefined)
  useEffect(() => {
    let alive = true
    listJobs({ limit: 1 }).then((l) => alive && setRecent(l[0] ?? null)).catch(() => alive && setRecent(null))
    return () => { alive = false }
  }, [])
  const waiting = queue?.count ?? 0

  return (
    <div className="mx-auto max-w-xl p-5 lg:p-10" data-testid="current-empty">
      <div className="rounded-xl border border-dashed border-input bg-paper px-6 py-10 text-center">
        <Activity className="mx-auto size-7 text-muted-foreground" />
        <p className="mt-3 text-base font-semibold">Nothing is running</p>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {waiting ? `${waiting} job${waiting === 1 ? ' is' : 's are'} in the queue${queue.waiting ? '' : ', all paused'}.` : 'Add a job and it starts right away.'}
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button size="sm" onClick={onCreate}><PlusCircle /> Create a job</Button>
          {waiting > 0 && <Button size="sm" variant="outline" onClick={onOpenQueue}><ListOrdered /> Open job queue</Button>}
        </div>
      </div>
      {recent && (
        <button
          type="button"
          onClick={() => onOpenJob(recent.id)}
          data-testid="current-recent-job"
          className="group mt-4 flex w-full items-center gap-3 rounded-lg border border-border bg-card px-3.5 py-3 text-left hover:border-input hover:shadow-md"
        >
          <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Most recent</span>
          <StatusChip status={recent.status} testId="current-recent-status" />
          <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{recent.title}</span>
          <span className="text-xs text-muted-foreground">{formatDateTime(recent.finished_at || recent.created_at)}</span>
          <ArrowRight className="size-4 text-muted-foreground group-hover:translate-x-0.5" />
        </button>
      )}
    </div>
  )
}

// Current job tab: the running job's full view. When it finishes, its final state
// stays visible (with a note) until the next job starts.
export default function CurrentJobTab({ queue, lastRunId, onOpenJob, onOpenQueue, onCreate, onQueueChanged }) {
  const runningId = queue?.running?.job_id ?? null
  const shownId = runningId || lastRunId
  if (!shownId) return <EmptyState queue={queue} onOpenJob={onOpenJob} onOpenQueue={onOpenQueue} onCreate={onCreate} />
  const banner = !runningId && (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-paper px-3.5 py-2.5 text-[13px] text-muted-foreground" data-testid="current-finished-note">
      <Activity className="size-4" /> Nothing is running now - this is the last job that ran.
      {queue?.count ? <Button size="xs" variant="outline" onClick={onOpenQueue} className="ml-auto">Queue ({queue.count})</Button> : null}
    </div>
  )
  return <JobView key={shownId} jobId={shownId} banner={banner} onOpenJob={onOpenJob} onOpenQueue={onOpenQueue} onQueueChanged={onQueueChanged} />
}
