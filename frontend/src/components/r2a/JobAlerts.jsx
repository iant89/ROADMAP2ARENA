import { Ban, CircleCheckBig, CircleStop, Download, TriangleAlert } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

export default function JobAlerts({ job, onDownload }) {
  if (!job) return null
  if (job.status === 'error') {
    return (
      <Alert className="r2a-rise border-coral/40 bg-coral-soft px-4 py-3.5 text-coral" data-testid="job-error-alert">
        <TriangleAlert />
        <AlertTitle className="font-semibold">
          Step {job.failed_step ?? '?'} failed{job.failed_step ? ` - ${job.steps[job.failed_step - 1]?.title}` : ''}
        </AlertTitle>
        <AlertDescription className="space-y-1.5 text-[13px] text-foreground/85">
          <p className="font-mono text-[12px] break-words" data-testid="job-error-text">{(job.error || '').replace(/^Step \d+ failed: /, '')}</p>
          {job.error_hint && !/Chrome tab/i.test(job.error || '') && <p className="font-medium text-coral">Hint: {job.error_hint}</p>}
          <p className="text-muted-foreground">Later steps were left pending. Files from completed steps can still be downloaded, and Resume retries from step {job.failed_step ?? job.steps.find((s) => s.status !== 'done')?.index}.</p>
        </AlertDescription>
      </Alert>
    )
  }
  if (job.status === 'stopped') {
    const next = job.steps.find((s) => s.status !== 'done')
    return (
      <Alert className="r2a-rise border-stop/35 bg-stop-soft px-4 py-3.5 text-stop" data-testid="job-stopped-alert">
        <CircleStop />
        <AlertTitle className="font-semibold">
          Stopped{job.stopped_step ? ` at step ${job.stopped_step} - ${job.steps[job.stopped_step - 1]?.title}` : ''}
        </AlertTitle>
        <AlertDescription className="text-[13px] text-foreground/85">
          {job.steps_done} of {job.step_total} steps finished.
          {next ? ` Resume continues from step ${next.index}; Restart starts a new job from step 1.` : ''}
        </AlertDescription>
      </Alert>
    )
  }
  if (job.status === 'cancelled') {
    return (
      <Alert className="r2a-rise border-border bg-muted px-4 py-3.5 text-slate" data-testid="job-cancelled-alert">
        <Ban />
        <AlertTitle className="font-semibold">Removed from the queue</AlertTitle>
        <AlertDescription className="text-[13px] text-foreground/85">
          {job.steps_done} of {job.step_total} steps finished. Resume puts it back in the queue; Restart queues a fresh copy.
        </AlertDescription>
      </Alert>
    )
  }
  if (job.status === 'done') {
    return (
      <Alert className="r2a-rise border-teal/35 bg-teal-soft px-4 py-3.5 text-teal" data-testid="job-done-alert">
        <CircleCheckBig />
        <AlertTitle className="font-semibold">All {job.step_total} steps done</AlertTitle>
        <AlertDescription className="flex flex-wrap items-center gap-3 text-[13px] text-foreground/85">
          <span>{job.artifacts.length} files are ready to download.</span>
          <Button size="sm" onClick={onDownload} className="bg-teal text-white hover:bg-teal/90">
            <Download /> Download ZIP
          </Button>
        </AlertDescription>
      </Alert>
    )
  }
  return null
}
