import { useState } from 'react'
import { CircleAlert, PlusCircle, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

function Field({ id, label, hint, error, children }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={id} className="text-[13px] font-semibold">{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
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

function isHttpUrl(value) {
  try {
    const u = new URL(value.trim())
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.host)
  } catch {
    return false
  }
}

const inputCls = 'bg-card h-9'

// Create job form. Submitting enqueues the job; it starts at once when nothing is running.
export default function StartForm({ form, onChange, stepCount, onSubmit, onLoadSample, submitting, queueInfo, settings, onOpenSettings, submitLabel = 'Add to queue' }) {
  // Inline errors appear once a field has been edited (avoids a flash before config defaults load).
  const [touched, setTouched] = useState({})
  const set = (key) => (e) => {
    setTouched((t) => (t[key] ? t : { ...t, [key]: true }))
    onChange({ ...form, [key]: e.target.value })
  }
  const modelError = form.model.trim() ? null : 'Model is required'
  const urlError = !form.arena_url.trim()
    ? 'arena2api base URL is required'
    : isHttpUrl(form.arena_url) ? null : 'Use an http:// or https:// URL'
  const disabled = submitting || stepCount === 0 || Boolean(urlError) || Boolean(modelError)
  const ahead = (queueInfo?.running ? 1 : 0) + (queueInfo?.waiting ?? 0)
  const queueHint = !queueInfo
    ? ''
    : ahead === 0 ? 'Nothing is running - it starts right away.'
      : `${ahead} job${ahead === 1 ? '' : 's'} ahead - it waits its turn in the queue.`

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-[1.4fr_1fr]">
        <Field id="arena_url" label="arena2api base URL" error={touched.arena_url ? urlError : null}>
          <Input id="arena_url" data-testid="arena-url-input" aria-invalid={Boolean(touched.arena_url && urlError)} aria-describedby={touched.arena_url && urlError ? 'arena_url-error' : undefined} className={cn(inputCls, 'font-mono text-[13px]')} value={form.arena_url} onChange={set('arena_url')} placeholder="http://localhost:9090" />
        </Field>
        <Field id="model" label="Model" error={touched.model ? modelError : null}>
          <Input id="model" data-testid="model-input" aria-invalid={Boolean(touched.model && modelError)} aria-describedby={touched.model && modelError ? 'model-error' : undefined} className={cn(inputCls, 'font-mono text-[13px]')} value={form.model} onChange={set('model')} placeholder="gpt-4o" />
        </Field>
      </div>
      <Field id="project_context" label="Project context" hint="Sent with step 1">
        <Textarea
          id="project_context"
          data-testid="project-context-input"
          className="field-sizing-fixed h-20 resize-y bg-card"
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
          className="field-sizing-fixed h-60 resize-y bg-card font-mono text-[12.5px] leading-relaxed"
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
