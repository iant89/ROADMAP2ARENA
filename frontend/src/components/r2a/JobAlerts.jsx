import { CircleCheckBig, Download, TriangleAlert } from 'lucide-react'
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
          <p className="text-muted-foreground">Later steps were left pending. Files from completed steps can still be downloaded.</p>
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
