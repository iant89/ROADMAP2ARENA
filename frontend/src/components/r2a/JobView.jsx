import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ArrowLeft, Download, FileText, ListOrdered } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { ScrollArea } from '@/components/ui/scroll-area'
import { downloadZip, restartJob, resumeJob, stopJob } from '@/lib/api'
import { useJob } from '@/hooks/useJob'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { cn } from '@/lib/utils'
import JobControls from './JobControls'
import JobAlerts from './JobAlerts'
import RoadmapChecklist from './RoadmapChecklist'
import WorkspaceTabs from './WorkspaceTabs'
import { StatusChip, formatDateTime } from './status'

function QueuedAlert({ job, onOpenQueue }) {
  const paused = job.status === 'paused'
  return (
    <Alert className={cn('r2a-rise px-4 py-3.5', paused ? 'border-pause/40 bg-pause-soft text-pause' : 'border-queue/30 bg-queue-soft text-queue')} data-testid="job-queued-alert">
      <ListOrdered />
      <AlertTitle className="font-semibold">
        {paused ? 'Paused in the queue' : 'Waiting in the queue'}{job.queue_position ? ` - position ${job.queue_position}` : ''}
      </AlertTitle>
      <AlertDescription className="space-y-2 text-[13px] text-foreground/85">
        <p>{paused ? 'This job will not run until it is unpaused.' : 'It starts automatically when the jobs ahead of it finish.'}</p>
        <Button size="sm" variant="outline" onClick={onOpenQueue} data-testid="open-queue-button"><ListOrdered /> Open job queue</Button>
      </AlertDescription>
    </Alert>
  )
}

// Full view of one job: checklist + controls on the left, Artifacts/Transcript/Log on the right.
// Used by the Current job tab and when a job is opened from Job history.
export default function JobView({ jobId, onBack, backLabel, onOpenJob, onOpenQueue, onQueueChanged, banner }) {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const { job, error, refresh, restartPolling } = useJob(jobId)
  const [busy, setBusy] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [tab, setTab] = useState('artifacts')
  const [selectedPath, setSelectedPath] = useState(null)
  const running = job?.status === 'running'

  useEffect(() => { setSelectedPath(null) }, [jobId])

  // Keep a file selected: follow the newest artifact while running.
  useEffect(() => {
    if (!job?.artifacts.length) return
    const exists = job.artifacts.some((a) => a.path === selectedPath)
    if (!exists || (running && selectedPath === null)) {
      const latest = [...job.artifacts].sort((a, b) => b.step_index - a.step_index)[0]
      setSelectedPath(latest.path)
    }
  }, [job, running, selectedPath])

  const act = async (fn) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false); onQueueChanged?.() }
  }

  const handleStop = () => act(async () => {
    try {
      const res = await stopJob(job.id)
      toast(`Job stopped${res.stopped_step ? ` at step ${res.stopped_step}` : ''}`, { description: `${res.steps_done} of ${job.step_total} steps finished. Resume or Restart when ready.` })
    } catch (err) {
      toast.error('Could not stop job', { description: err.message })
    } finally { refresh() }
  })

  const handleResume = (overrides) => act(async () => {
    try {
      const res = await resumeJob(job.id, overrides)
      toast.success(res.status === 'running' ? `Resumed from step ${res.resumed_from_step}` : `Resume queued at position ${res.queue_position}`,
        { description: 'Earlier steps are replayed as conversation history.' })
      restartPolling()
    } catch (err) {
      toast.error('Could not resume job', { description: err.message })
    }
  })

  const handleRestart = (overrides) => act(async () => {
    try {
      const res = await restartJob(job.id, overrides)
      toast.success(res.status === 'running' ? 'Restarted as a new job' : `Restart queued at position ${res.queue_position}`,
        { description: `New job ${res.id.slice(0, 8)} runs all ${job.step_total} steps from the beginning.` })
      onOpenJob?.(res.id, res.status)
    } catch (err) {
      toast.error('Could not restart job', { description: err.message })
    }
  })

  const handleDownload = useCallback(async () => {
    if (!job) return
    setDownloading(true)
    try {
      const res = await downloadZip(job.id)
      toast.success('ZIP downloaded', { description: `${res.filename} (${Math.max(1, Math.round(res.size / 1024))} KB) - skipped paths are listed in the Log` })
    } catch (err) {
      toast.error('ZIP failed', { description: err.message })
    } finally {
      setDownloading(false)
      refresh()
    }
  }, [job, refresh])

  if (!job) {
    return (
      <div className="space-y-3 p-6" data-testid="job-view-loading">
        {error ? <p className="text-sm text-coral">Could not load job {jobId}: {error}</p> : [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
        {onBack && <Button variant="outline" size="sm" onClick={onBack}><ArrowLeft /> {backLabel}</Button>}
      </div>
    )
  }

  const canDownload = job.steps_done >= 1
  const left = (
    <div className="space-y-5 p-5 lg:p-7" data-testid="job-view">
      {onBack && (
        <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2" data-testid="job-view-back"><ArrowLeft /> {backLabel}</Button>
      )}
      {banner}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <StatusChip status={job.status} testId="job-view-status" />
            <span className="text-xs text-muted-foreground">{job.steps_done}/{job.step_total} steps</span>
          </div>
          <h2 className="mt-2 truncate text-lg font-semibold tracking-tight" data-testid="job-view-title">{job.title || 'Untitled roadmap'}</h2>
          <p className="text-[12.5px] text-muted-foreground">
            <span className="font-mono">{job.id}</span>
            {job.restarted_from && <span className="block">restart of <span className="font-mono">{job.restarted_from.slice(0, 8)}</span></span>}
          </p>
        </div>
        <Button
          size="sm"
          onClick={handleDownload}
          disabled={!canDownload || downloading}
          data-testid="download-zip-button"
          className={cn('hover:-translate-y-px', job.status === 'done' && 'r2a-pulse bg-teal text-white hover:bg-teal/90')}
        >
          <Download /> {downloading ? 'Packing...' : 'Download ZIP'}
        </Button>
      </div>

      <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3.5 py-2.5 text-[13px]" data-testid="job-inputs-summary">
        <FileText className="size-4 shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate">
          <span className="font-mono font-medium">{job.config.model}</span>
          <span className="mx-2 text-muted-foreground">via</span>
          <span className="font-mono text-muted-foreground">{job.config.arena_url}</span>
        </p>
        <span className="shrink-0 text-xs text-muted-foreground">{formatDateTime(job.created_at)}</span>
      </div>

      <JobControls job={job} busy={busy} onStop={handleStop} onResume={handleResume} onRestart={handleRestart} />
      {(job.status === 'queued' || job.status === 'paused') && <QueuedAlert job={job} onOpenQueue={onOpenQueue} />}
      <JobAlerts job={job} onDownload={handleDownload} />
      <Separator />
      <RoadmapChecklist steps={job.steps} job={job} />
    </div>
  )
  const right = <WorkspaceTabs job={job} tab={tab} setTab={setTab} selectedPath={selectedPath} setSelectedPath={setSelectedPath} />

  if (!isDesktop) {
    return (
      <div className="flex flex-col gap-2">
        {left}
        <section className="mx-3 mb-6 h-[78vh] overflow-hidden rounded-xl border border-border bg-card shadow-sm">{right}</section>
      </div>
    )
  }
  return (
    <ResizablePanelGroup orientation="horizontal" className="h-full">
      <ResizablePanel defaultSize="40%" minSize="28%" maxSize="60%" className="min-h-0">
        <ScrollArea className="h-full">{left}</ScrollArea>
      </ResizablePanel>
      <ResizableHandle withHandle className="bg-border hover:bg-input" />
      <ResizablePanel defaultSize="60%" minSize="35%" className="min-h-0 p-4 pl-3">
        <section className="h-full overflow-hidden rounded-xl border border-border bg-card shadow-sm">{right}</section>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
