import { useEffect, useState } from 'react'
import { Play, RotateCcw, Settings2, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

const CONFIRM_MS = 4000

function isHttpUrl(value) {
  try {
    const u = new URL(value.trim())
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.host)
  } catch {
    return false
  }
}

// Stop (two-click confirm) while running; Resume (error/stopped/cancelled) and
// Restart (any finished job) with optional model/URL overrides. Both go through
// the queue: they start at once when nothing is running, otherwise they wait.
export default function JobControls({ job, busy, onStop, onResume, onRestart }) {
  const [confirmStop, setConfirmStop] = useState(false)
  const [showOverrides, setShowOverrides] = useState(false)
  const [overrides, setOverrides] = useState({ arena_url: job.config.arena_url, model: job.config.model })

  useEffect(() => {
    setOverrides({ arena_url: job.config.arena_url, model: job.config.model })
  }, [job.id, job.config.arena_url, job.config.model])

  useEffect(() => {
    if (!confirmStop) return undefined
    const t = setTimeout(() => setConfirmStop(false), CONFIRM_MS)
    return () => clearTimeout(t)
  }, [confirmStop])

  const running = job.status === 'running'
  const canResume = ['error', 'stopped', 'cancelled'].includes(job.status)
  const canRestart = ['done', 'error', 'stopped', 'cancelled'].includes(job.status)
  const modelError = overrides.model.trim() ? null : 'Model is required'
  const urlError = isHttpUrl(overrides.arena_url) ? null : 'Use an http:// or https:// URL'
  const invalid = showOverrides && Boolean(modelError || urlError)
  const payload = showOverrides ? overrides : undefined
  const resumeFrom = job.steps.find((s) => s.status !== 'done')?.index

  if (running) {
    return (
      <div className="flex flex-wrap items-center gap-2" data-testid="job-controls">
        {confirmStop ? (
          <>
            <Button size="sm" onClick={() => { setConfirmStop(false); onStop() }} disabled={busy} data-testid="confirm-stop-button" className="bg-coral text-white hover:bg-coral/90">
              <Square className="fill-current" /> Confirm stop
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmStop(false)} data-testid="cancel-stop-button">
              <X /> Keep running
            </Button>
            <span className="text-xs text-muted-foreground">The step in flight is abandoned; finished steps are kept.</span>
          </>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setConfirmStop(true)} disabled={busy} data-testid="stop-job-button" className="border-coral/40 text-coral hover:bg-coral-soft hover:text-coral">
            <Square className="fill-current" /> Stop job
          </Button>
        )}
      </div>
    )
  }

  if (!canResume && !canRestart) return null

  return (
    <Collapsible open={showOverrides} onOpenChange={setShowOverrides} className="space-y-3" data-testid="job-controls">
      <div className="flex flex-wrap items-center gap-2">
        {canResume && (
          <Button size="sm" onClick={() => onResume(payload)} disabled={busy || invalid} data-testid="resume-job-button" className="hover:-translate-y-px">
            <Play /> Resume{resumeFrom ? ` from step ${resumeFrom}` : ''}
          </Button>
        )}
        {canRestart && (
          <Button size="sm" variant="outline" onClick={() => onRestart(payload)} disabled={busy || invalid} data-testid="restart-job-button" className="hover:-translate-y-px">
            <RotateCcw /> Restart as new job
          </Button>
        )}
        <CollapsibleTrigger asChild>
          <Button size="sm" variant="ghost" data-testid="toggle-overrides-button">
            <Settings2 /> {showOverrides ? 'Keep model and URL' : 'Change model or URL'}
          </Button>
        </CollapsibleTrigger>
      </div>
      <p className="text-xs text-muted-foreground">If another job is running, this is added to the job queue.</p>
      <CollapsibleContent className="r2a-rise">
        <div className="grid gap-3 rounded-lg border border-border bg-paper p-3.5 sm:grid-cols-[1.4fr_1fr]" data-testid="overrides-panel">
          <div className="space-y-1.5">
            <Label htmlFor="ovr-url" className="text-xs font-semibold">arena2api base URL</Label>
            <Input id="ovr-url" data-testid="override-url-input" aria-invalid={Boolean(urlError)} className="h-8 bg-card font-mono text-[12.5px]" value={overrides.arena_url} onChange={(e) => setOverrides((o) => ({ ...o, arena_url: e.target.value }))} />
            {urlError && <p className="text-xs font-medium text-coral">{urlError}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ovr-model" className="text-xs font-semibold">Model</Label>
            <Input id="ovr-model" data-testid="override-model-input" aria-invalid={Boolean(modelError)} className={cn('h-8 bg-card font-mono text-[12.5px]')} value={overrides.model} onChange={(e) => setOverrides((o) => ({ ...o, model: e.target.value }))} />
            {modelError && <p className="text-xs font-medium text-coral">{modelError}</p>}
          </div>
          <p className="text-xs text-muted-foreground sm:col-span-2">Applied to the next Resume or Restart.</p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
