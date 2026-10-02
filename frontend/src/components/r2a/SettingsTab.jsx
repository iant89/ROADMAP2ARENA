import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { CircleAlert, RotateCcw, Save, Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { getSettings, resetSettings, saveSettings } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatDateTime } from './status'

const FIELDS = [
  { key: 'arena_url', label: 'Default arena2api base URL', mono: true, hint: 'Pre-filled in the Create job form.' },
  { key: 'model', label: 'Default model', mono: true, hint: 'Pre-filled in the Create job form.' },
  { key: 'step_delay_seconds', label: 'Step delay (seconds)', number: true, min: 0, max: 600, hint: 'Pause between steps, 0-600.' },
  { key: 'request_timeout_seconds', label: 'Request timeout (seconds)', number: true, min: 10, max: 3600, hint: 'Per arena2api request, 10-3600.' },
]

function isHttpUrl(value) {
  try {
    const u = new URL(value.trim())
    return (u.protocol === 'http:' || u.protocol === 'https:') && Boolean(u.host)
  } catch {
    return false
  }
}

function validate(values) {
  const errors = {}
  if (!isHttpUrl(values.arena_url)) errors.arena_url = 'Use an http:// or https:// URL'
  if (!values.model.trim()) errors.model = 'Model is required'
  for (const f of FIELDS.filter((x) => x.number)) {
    const raw = String(values[f.key]).trim()
    const n = Number(raw)
    if (raw === '' || !Number.isFinite(n) || n < f.min || n > f.max) errors[f.key] = `Enter a number from ${f.min} to ${f.max}`
  }
  return errors
}

const toForm = (s) => ({ arena_url: s.arena_url, model: s.model, step_delay_seconds: String(s.step_delay_seconds), request_timeout_seconds: String(s.request_timeout_seconds) })

// Settings tab: runtime defaults stored in MongoDB (GET/PUT /api/settings).
export default function SettingsTab({ onSaved }) {
  const [saved, setSaved] = useState(null)
  const [values, setValues] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  useEffect(() => {
    let alive = true
    getSettings()
      .then((s) => { if (alive) { setSaved(s); setValues(toForm(s)) } })
      .catch((err) => alive && setLoadError(err.message))
    return () => { alive = false }
  }, [])

  if (!values) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-5 lg:p-7">
        {loadError ? <p className="text-sm text-coral">Could not load settings: {loadError}</p> : [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14 rounded-lg" />)}
      </div>
    )
  }

  const errors = validate(values)
  const dirty = JSON.stringify(values) !== JSON.stringify(toForm(saved))
  const env = saved.env_defaults

  const apply = (s, message) => {
    setSaved(s)
    setValues(toForm(s))
    onSaved(s)
    toast.success(message, { description: `Model ${s.model}, delay ${s.step_delay_seconds}s, timeout ${s.request_timeout_seconds}s - used by the next job run.` })
  }

  const handleSave = async () => {
    setBusy(true)
    try {
      apply(await saveSettings({
        arena_url: values.arena_url.trim(),
        model: values.model.trim(),
        step_delay_seconds: Number(values.step_delay_seconds),
        request_timeout_seconds: Number(values.request_timeout_seconds),
      }), 'Settings saved')
    } catch (err) {
      toast.error('Could not save settings', { description: err.message })
    } finally {
      setBusy(false)
    }
  }

  const handleReset = async () => {
    setConfirmReset(false)
    setBusy(true)
    try {
      apply(await resetSettings(), 'Settings reset to .env defaults')
    } catch (err) {
      toast.error('Could not reset settings', { description: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-5 lg:p-7" data-testid="settings-tab">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight"><Settings className="size-5" /> Settings</h2>
        <p className="text-[13px] text-muted-foreground">
          Stored on the backend and seeded from <code className="rounded bg-muted px-1 py-0.5 text-[12px]">backend/.env</code>. Changes apply to the next job run; a running job keeps its values.
        </p>
      </div>
      <section className="space-y-5 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6">
        {FIELDS.map((f) => {
          const id = `setting-${f.key}`
          const err = errors[f.key]
          return (
            <div key={f.key} className="space-y-1.5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <Label htmlFor={id} className="text-[13px] font-semibold">{f.label}</Label>
                <span className="text-xs text-muted-foreground">.env default: <span className="font-mono">{String(env[f.key])}</span></span>
              </div>
              <Input
                id={id}
                data-testid={`${id}-input`}
                type={f.number ? 'number' : 'text'}
                inputMode={f.number ? 'decimal' : undefined}
                min={f.min}
                max={f.max}
                step="any"
                aria-invalid={Boolean(err)}
                className={cn('h-9 bg-card', (f.mono || f.number) && 'font-mono text-[13px]', f.number && 'max-w-[200px]')}
                value={values[f.key]}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
              {err
                ? <p role="alert" data-testid={`${id}-error`} className="flex items-center gap-1.5 text-xs font-medium text-coral"><CircleAlert className="size-3.5" /> {err}</p>
                : <p className="text-xs text-muted-foreground">{f.hint}</p>}
            </div>
          )
        })}
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button onClick={handleSave} disabled={busy || !dirty || Object.keys(errors).length > 0} data-testid="settings-save"><Save /> Save settings</Button>
          {confirmReset ? (
            <span className="flex items-center gap-2" data-testid="settings-reset-confirm">
              <span className="text-xs font-medium">Replace all four values with the .env defaults?</span>
              <Button size="sm" variant="outline" onClick={handleReset} disabled={busy} data-testid="settings-reset-yes">Reset</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmReset(false)}>Cancel</Button>
            </span>
          ) : (
            <Button variant="outline" onClick={() => setConfirmReset(true)} disabled={busy} data-testid="settings-reset"><RotateCcw /> Reset to .env defaults</Button>
          )}
          {dirty && !confirmReset && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
          {saved.updated_at && <span className="ml-auto text-xs text-muted-foreground">Last saved {formatDateTime(saved.updated_at)}</span>}
        </div>
      </section>
    </div>
  )
}
