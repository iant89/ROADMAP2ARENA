import { useState } from 'react'
import { CircleAlert, PlusCircle, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useProviders } from '@/hooks/useProviders'
import { modelError as modelErrorOf, providerError } from '@/lib/providerForm'
import ProviderModelFields from './ProviderModelFields'

function Field({ id, label, hint, error, children }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <Label htmlFor={id} className="shrink-0 text-[13px] font-semibold">{label}</Label>
        {hint && <span className="min-w-0 truncate text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
      {error && (
        <p id={`${id}-error`} role="alert" data-testid={`${id}-error`} className="r2a-rise flex items-center gap-1.5 text-xs font-medium text-coral">
          <CircleAlert className="size-3.5 shrink-0" /> {error}
        </p>
      )}
    </div>
  )
}

// One size and font for every field (text-base on mobile avoids iOS focus zoom).
const fieldText = 'font-mono text-base sm:text-sm'

// Create job form. Submitting enqueues the job; it starts at once when nothing is running.
export default function StartForm({ form, onChange, stepCount, onSubmit, onLoadSample, submitting, queueInfo, settings, onOpenSettings, submitLabel = 'Add to queue', legacyUrl = null }) {
  const { data: providerData, error: providersError } = useProviders()
  const providers = providerData?.providers ?? null
  // Inline errors appear once a field has been edited (avoids a flash before config defaults load).
  const [touched, setTouched] = useState({})
  const set = (key) => (e) => {
    setTouched((t) => (t[key] ? t : { ...t, [key]: true }))
    onChange({ ...form, [key]: e.target.value })
  }
  const modelError = modelErrorOf(form)
  const provError = providerError(form, providers)
  const disabled = submitting || stepCount === 0 || !providers || Boolean(provError) || Boolean(modelError)
  const ahead = (queueInfo?.running ? 1 : 0) + (queueInfo?.waiting ?? 0)
  const queueHint = !queueInfo
    ? ''
    : ahead === 0 ? 'Nothing is running - it starts right away.'
      : `${ahead} job${ahead === 1 ? '' : 's'} ahead - it waits its turn in the queue.`

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <ProviderModelFields
          value={form}
          onChange={(next) => {
            if (next.model !== form.model) setTouched((t) => (t.model ? t : { ...t, model: true }))
            onChange(next)
          }}
          providers={providers}
          fallbackModel={settings?.model || ''}
          legacyUrl={legacyUrl}
          showErrors={{ provider: Boolean(providers), model: Boolean(touched.model) }}
        />
        {providersError && !providers && <p className="text-xs font-medium text-coral" data-testid="providers-load-error">Could not load providers: {providersError}</p>}
        {providers && providers.length === 0 && onOpenSettings && (
          <p className="text-xs text-muted-foreground" data-testid="no-providers-hint">
            No providers yet - <button type="button" className="font-medium text-foreground underline underline-offset-2" onClick={onOpenSettings}>add one in Settings</button>.
          </p>
        )}
      </div>
      <Field id="project_context" label="Project context" hint="Sent with step 1">
        <Textarea
          id="project_context"
          data-testid="project-context-input"
          className={cn('field-sizing-fixed h-20 resize-y bg-card', fieldText)}
          value={form.project_context}
          onChange={set('project_context')}
          placeholder="Stack, constraints, naming conventions..."
        />
      </Field>
      <Field
        id="roadmap_md"
        label="ROADMAP.md"
        hint={
          <span data-testid="steps-found" className={cn('font-medium', stepCount ? 'text-teal' : 'text-muted-foreground')}>
            {stepCount ? `${stepCount} step${stepCount === 1 ? '' : 's'} found` : 'No steps found'}
          </span>
        }
      >
        <Textarea
          id="roadmap_md"
          data-testid="roadmap-input"
          className={cn('field-sizing-fixed h-60 resize-y bg-card leading-relaxed', fieldText)}
          value={form.roadmap_md}
          onChange={set('roadmap_md')}
          placeholder={'### Project skeleton\nCreate the package layout...\n\n- [ ] Add tests'}
          spellCheck={false}
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" onClick={onSubmit} disabled={disabled} data-testid="start-job-button" className="px-4 hover:-translate-y-px">
          <PlusCircle /> {submitting ? 'Adding...' : submitLabel}
        </Button>
        {onLoadSample && (
          <Button size="lg" variant="outline" onClick={onLoadSample} data-testid="load-sample-button" className="px-3 hover:-translate-y-px">
            <Sparkles /> Load sample roadmap
          </Button>
        )}
        <span className="text-xs text-muted-foreground" data-testid="queue-hint">{queueHint}</span>
      </div>
      {settings && (
        <p className="text-xs text-muted-foreground" data-testid="run-settings-hint">
          Runs use a {settings.step_delay_seconds}s pause between steps and a {settings.request_timeout_seconds}s request timeout
          {onOpenSettings ? <>{' '}(<button type="button" className="font-medium text-foreground underline underline-offset-2" onClick={onOpenSettings}>change in Settings</button>).</> : ' (set in Settings).'}
        </p>
      )}
    </div>
  )
}
