import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Bell, Mail, Save, Send, Undo2, Webhook } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { getNotificationSettings, saveNotificationSettings, testEmail, testWebhook } from '@/lib/api'
import { browserPermission, browserPref, browserSupported, enableBrowser, setBrowserPref } from '@/lib/browserNotify'
import { cn } from '@/lib/utils'
import { formatDateTime } from './status'

const EVENTS = [['job_done', 'Job finished'], ['job_failed', 'Job failed'], ['job_stopped', 'Job stopped'], ['queue_empty', 'Queue empty']]
const CHANNELS = [['in_app', 'In-app'], ['webhook', 'Webhook'], ['email', 'Email']]
const fieldCls = 'h-9 bg-card font-mono text-base sm:text-sm'

function toForm(s) {
  return { ...s, email: { ...s.email, to_addrs: s.email.to_addrs.join(', '), password: '' } }
}

function Check({ checked, onChange, testId, label, disabled }) {
  return (
    <input type="checkbox" className="size-4 accent-primary" checked={checked} disabled={disabled} aria-label={label}
      onChange={(e) => onChange(e.target.checked)} data-testid={testId} />
  )
}

function Field({ id, label, hint, children, className }) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <Label htmlFor={id} className="shrink-0 text-[13px] font-semibold">{label}</Label>
        {hint && <span className="min-w-0 truncate text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

function TestResult({ res, testId }) {
  if (!res) return null
  return (
    <span className={cn('text-xs font-medium', res.ok ? 'text-teal' : 'text-coral')} data-testid={testId} role="status">
      {res.ok ? `Sent${res.status_code ? ` (HTTP ${res.status_code})` : ''}` : `Failed: ${res.error}`}
    </span>
  )
}

// Settings > Notifications: per-event toggles per channel, browser notifications (this browser),
// webhook and SMTP email with "Send test". The SMTP password is write-only.
export default function NotificationSettings() {
  const [saved, setSaved] = useState(null)
  const [form, setForm] = useState(null)
  const [pwMode, setPwMode] = useState('keep') // keep | set | clear
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [tests, setTests] = useState({})
  const [perm, setPerm] = useState(browserPermission())
  const [browserOn, setBrowserOn] = useState(browserPref())

  const load = (s) => { setSaved(s); setForm(toForm(s)); setPwMode('keep'); setError(null) }
  useEffect(() => { getNotificationSettings().then(load).catch((err) => setError(err.message)) }, [])

  if (!form) {
    return (
      <section className="rounded-xl border border-border bg-card p-5" data-testid="notif-settings">
        {error ? <p className="text-sm text-coral">Could not load notification settings: {error}</p> : <Skeleton className="h-40 rounded-lg" />}
      </section>
    )
  }

  const set = (section, key, value) => setForm((f) => ({ ...f, [section]: { ...f[section], [key]: value } }))
  const setEvent = (ch, ev, v) => setForm((f) => ({ ...f, [ch]: { ...f[ch], events: { ...f[ch].events, [ev]: v } } }))
  const patch = () => {
    const { password_set: _a, password_masked: _b, password, ...email } = form.email
    const pw = pwMode === 'set' ? { password } : pwMode === 'clear' ? { password: '' } : {}
    return { app_url: form.app_url, in_app: form.in_app, webhook: form.webhook, email: { ...email, ...pw } }
  }
  const dirty = pwMode !== 'keep' || JSON.stringify(toForm(saved)) !== JSON.stringify({ ...form, email: { ...form.email, password: '' } })

  const save = async () => {
    setBusy(true); setError(null)
    try {
      load(await saveNotificationSettings(patch()))
      toast.success('Notification settings saved')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  const runTest = async (kind) => {
    setTests((t) => ({ ...t, [kind]: { running: true } }))
    try {
      const p = patch()
      const res = kind === 'webhook' ? await testWebhook({ url: p.webhook.url }) : await testEmail((({ enabled: _e, events: _v, ...rest }) => rest)(p.email))
      setTests((t) => ({ ...t, [kind]: res }))
    } catch (err) {
      setTests((t) => ({ ...t, [kind]: { ok: false, error: err.message } }))
    }
  }
  const toggleBrowser = async (on) => {
    if (!on) { setBrowserPref(false); setBrowserOn(false); return }
    const p = await enableBrowser()
    setPerm(p); setBrowserOn(p === 'granted')
    if (p === 'denied') toast.error('Browser notifications are blocked', { description: 'Allow them for this site in the browser settings.' })
  }
  const e = form.email
  const pwPlaceholder = pwMode === 'clear' ? 'will be cleared on save' : saved.email.password_set ? `${saved.email.password_masked} (saved - leave blank to keep)` : 'not set'

  return (
    <section className="space-y-6 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6" data-testid="notif-settings">
      <div>
        <h3 className="flex items-center gap-2 text-[15px] font-semibold"><Bell className="size-4" /> Notifications</h3>
        <p className="text-[13px] text-muted-foreground">In-app notifications appear as toasts and in the bell. Webhook and email failures are logged in the job log and never affect jobs.</p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[320px] text-[13px]" data-testid="notif-event-matrix">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1.5 font-medium">Event</th>
              {CHANNELS.map(([ch, label]) => <th key={ch} className="w-20 py-1.5 text-center font-medium">{label}</th>)}
            </tr>
          </thead>
          <tbody>
            {EVENTS.map(([ev, label]) => (
              <tr key={ev} className="border-t border-border">
                <td className="py-2">{label}</td>
                {CHANNELS.map(([ch, chLabel]) => (
                  <td key={ch} className="text-center">
                    <Check checked={form[ch].events[ev]} onChange={(v) => setEvent(ch, ev, v)} label={`${chLabel}: ${label}`} testId={`notif-ev-${ch}-${ev}`} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-[13px]">
        <Check checked={browserOn && perm === 'granted'} onChange={toggleBrowser} disabled={!browserSupported() || perm === 'denied'} testId="notif-browser-toggle" label="Browser notifications" />
        <span className="font-semibold">Browser notifications</span>
        <span className="text-xs text-muted-foreground" data-testid="notif-browser-status">
          (this browser, while the tab is in the background - permission: {perm})
        </span>
      </label>

      <div className="space-y-3 border-t border-border pt-5">
        <label className="flex items-center gap-2 text-[13px] font-semibold">
          <Check checked={form.webhook.enabled} onChange={(v) => set('webhook', 'enabled', v)} testId="notif-webhook-enabled" label="Enable webhook" />
          <Webhook className="size-4" /> Webhook
        </label>
        <Field id="notif-webhook-url" label="Webhook URL" hint="POST JSON with Slack 'text' and Discord 'content'">
          <Input id="notif-webhook-url" className={fieldCls} value={form.webhook.url} placeholder="https://hooks.slack.com/services/..." onChange={(ev) => set('webhook', 'url', ev.target.value)} data-testid="notif-webhook-url" />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="outline" onClick={() => runTest('webhook')} disabled={!form.webhook.url || tests.webhook?.running} data-testid="notif-webhook-test"><Send /> {tests.webhook?.running ? 'Sending...' : 'Send test'}</Button>
          <TestResult res={tests.webhook?.running ? null : tests.webhook} testId="notif-webhook-result" />
        </div>
      </div>

      <div className="space-y-3 border-t border-border pt-5">
        <label className="flex items-center gap-2 text-[13px] font-semibold">
          <Check checked={e.enabled} onChange={(v) => set('email', 'enabled', v)} testId="notif-email-enabled" label="Enable email" />
          <Mail className="size-4" /> Email (SMTP)
        </label>
        <div className="grid gap-3 sm:grid-cols-[1fr_7rem_9rem]">
          <Field id="notif-smtp-host" label="SMTP host">
            <Input id="notif-smtp-host" className={fieldCls} value={e.host} placeholder="smtp.example.com" onChange={(ev) => set('email', 'host', ev.target.value)} data-testid="notif-smtp-host" />
          </Field>
          <Field id="notif-smtp-port" label="Port">
            <Input id="notif-smtp-port" className={fieldCls} type="number" min={1} max={65535} value={e.port} onChange={(ev) => set('email', 'port', ev.target.value === '' ? '' : Number(ev.target.value))} data-testid="notif-smtp-port" />
          </Field>
          <Field id="notif-smtp-security" label="Security">
            <select id="notif-smtp-security" className={cn(fieldCls, 'w-full rounded-md border border-input px-2')} value={e.security} onChange={(ev) => set('email', 'security', ev.target.value)} data-testid="notif-smtp-security">
              <option value="starttls">STARTTLS</option>
              <option value="ssl">SSL/TLS</option>
              <option value="none">None</option>
            </select>
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="notif-smtp-user" label="Username">
            <Input id="notif-smtp-user" className={fieldCls} value={e.username} autoComplete="off" onChange={(ev) => set('email', 'username', ev.target.value)} data-testid="notif-smtp-user" />
          </Field>
          <Field id="notif-smtp-password" label="Password" hint={saved.email.password_set ? <button type="button" className="underline" onClick={() => { setPwMode('clear'); set('email', 'password', '') }} data-testid="notif-smtp-password-clear">clear saved</button> : 'write-only'}>
            <Input id="notif-smtp-password" className={fieldCls} type="password" autoComplete="new-password" value={e.password} placeholder={pwPlaceholder}
              onChange={(ev) => { const v = ev.target.value; setPwMode((m) => (v ? 'set' : m === 'clear' ? 'clear' : 'keep')); set('email', 'password', v) }} data-testid="notif-smtp-password" />
          </Field>
          <Field id="notif-smtp-from" label="From">
            <Input id="notif-smtp-from" className={fieldCls} value={e.from_addr} placeholder="r2a@example.com" onChange={(ev) => set('email', 'from_addr', ev.target.value)} data-testid="notif-smtp-from" />
          </Field>
          <Field id="notif-smtp-to" label="To" hint="comma-separated">
            <Input id="notif-smtp-to" className={fieldCls} value={e.to_addrs} placeholder="you@example.com" onChange={(ev) => set('email', 'to_addrs', ev.target.value)} data-testid="notif-smtp-to" />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="outline" onClick={() => runTest('email')} disabled={!e.host || tests.email?.running} data-testid="notif-email-test"><Send /> {tests.email?.running ? 'Sending...' : 'Send test'}</Button>
          <TestResult res={tests.email?.running ? null : tests.email} testId="notif-email-result" />
        </div>
      </div>

      <Field id="notif-app-url" label="App URL" hint="used for links in webhook and email messages" className="border-t border-border pt-5">
        <Input id="notif-app-url" className={fieldCls} value={form.app_url} onChange={(ev) => setForm((f) => ({ ...f, app_url: ev.target.value }))} data-testid="notif-app-url" />
      </Field>

      {error && <p role="alert" className="text-xs font-medium text-coral" data-testid="notif-save-error">{error}</p>}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button onClick={save} disabled={busy || !dirty} data-testid="notif-save"><Save /> Save notification settings</Button>
        <Button variant="ghost" onClick={() => load(saved)} disabled={busy || !dirty} data-testid="notif-discard"><Undo2 /> Discard changes</Button>
        {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
        {saved.updated_at && <span className="ml-auto text-xs text-muted-foreground">Last saved {formatDateTime(saved.updated_at)}</span>}
      </div>
    </section>
  )
}
