import { useEffect, useState } from 'react'
import { Play, RotateCcw, Settings2, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { useProviders } from '@/hooks/useProviders'
import { modelError as modelErrorOf, providerError } from '@/lib/providerForm'
import ProviderModelFields from './ProviderModelFields'

const CONFIRM_MS = 4000

const initialOverrides = (job) => ({
  provider_id: job.config.provider?.id || '',
  arena_url: job.config.provider ? '' : job.config.arena_url,
  model: job.config.model,
})

// Stop (two-click confirm) while running; Resume (error/stopped/cancelled) and
// Restart (any finished job) with optional provider/model overrides (a legacy job may keep or
// change its own arena2api URL). Both go through
// the queue: they start at once when nothing is running, otherwise they wait.
export default function JobControls({ job, busy, onStop, onResume, onRestart }) {
  const [confirmStop, setConfirmStop] = useState(false)
  const [showOverrides, setShowOverrides] = useState(false)
  const [overrides, setOverrides] = useState(() => initialOverrides(job))
  const { data: providerData } = useProviders()
  const providers = providerData?.providers ?? null
  const providerId = job.config.provider?.id

  useEffect(() => {
    setOverrides(initialOverrides(job))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id, job.config.arena_url, job.config.model, providerId])

  useEffect(() => {
    if (!confirmStop) return undefined
    const t = setTimeout(() => setConfirmStop(false), CONFIRM_MS)
    return () => clearTimeout(t)
  }, [confirmStop])

  const running = job.status === 'running'
  const canResume = ['error', 'stopped', 'cancelled'].includes(job.status)
  const canRestart = ['done', 'error', 'stopped', 'cancelled'].includes(job.status)
  const modelError = modelErrorOf(overrides)
  const provError = providerError(overrides, providers)
  const invalid = showOverrides && (!providers || Boolean(modelError || provError))
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
            <Settings2 /> {showOverrides ? 'Keep provider and model' : 'Change provider or model'}
          </Button>
        </CollapsibleTrigger>
      </div>
      <p className="text-xs text-muted-foreground">If another job is running, this is added to the job queue.</p>
      <CollapsibleContent className="r2a-rise">
        <div className="space-y-3 rounded-lg border border-border bg-paper p-3.5" data-testid="overrides-panel">
          <ProviderModelFields
            value={overrides}
            onChange={setOverrides}
            providers={providers}
            legacyUrl={job.config.provider ? null : job.config.arena_url}
            idPrefix="override"
            compact
          />
          <p className="text-xs text-muted-foreground">Applied to the next Resume or Restart.</p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
