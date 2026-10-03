import { useState } from 'react'
import { toast } from 'sonner'
import { CircleAlert, KeyRound, LockKeyhole, Pencil, PlugZap, Plus, RefreshCw, Save, Server, Star, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { createProvider, deleteProvider, setDefaultProvider, testProvider, updateProvider } from '@/lib/api'
import { forgetModels, loadModels, useProviders } from '@/hooks/useProviders'
import { cn } from '@/lib/utils'
import ConfirmButton from './ConfirmButton'
import { isHttpUrl } from '@/lib/providerForm'

const KEY_CMD = '/app/venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
const fieldCls = 'h-9 bg-card font-mono text-base sm:text-[13px]'
const HEADER_NAME_RE = /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$/

const emptyDraft = (preset) => ({
  name: preset?.id && preset.id !== 'custom' ? preset.label : '',
  preset: preset?.id || 'custom',
  base_url: preset?.base_url || '',
  apiKey: '',
  keyMode: 'replace', // new provider: the key field is simply "the key"
  headers: [],
  default_model: '',
  make_default: false,
})

const draftFrom = (p) => ({
  name: p.name,
  preset: p.preset,
  base_url: p.base_url,
  apiKey: '',
  keyMode: p.api_key_set ? 'keep' : 'replace',
  headers: Object.entries(p.headers || {}).map(([k, v]) => ({ k, v })),
  default_model: p.default_model || '',
  make_default: false,
})

function validate(d) {
  const e = {}
  if (!d.name.trim()) e.name = 'Name is required'
  if (!isHttpUrl(d.base_url)) e.base_url = 'Use an http:// or https:// URL'
  const names = new Set()
  d.headers.forEach((h, i) => {
    const k = h.k.trim().toLowerCase()
    if (!k && !h.v.trim()) return
    if (!HEADER_NAME_RE.test(h.k.trim())) e[`h${i}`] = 'Invalid header name'
    else if (names.has(k)) e[`h${i}`] = 'Duplicate header'
    names.add(k)
  })
  return e
}

const headersOf = (d) => Object.fromEntries(d.headers.filter((h) => h.k.trim()).map((h) => [h.k.trim(), h.v]))

// Add/edit form for one provider. The API key is write-only: an existing key is never shown,
// only "saved" with Replace / Remove; leaving it alone keeps it.
function ProviderEditor({ provider, presets, encryption, onDone, onCancel }) {
  const [draft, setDraft] = useState(() => (provider ? draftFrom(provider) : emptyDraft(presets.find((p) => p.id === 'openai'))))
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState(null)
  const [models, setModels] = useState(null)
  const set = (patch) => { setDraft((d) => ({ ...d, ...patch })); setTest(null) }
  const preset = presets.find((p) => p.id === draft.preset) || presets.find((p) => p.id === 'custom')
  const errors = validate(draft)
  const invalid = Object.keys(errors).length > 0
  const sendingKey = draft.keyMode === 'replace' && draft.apiKey.trim() !== ''
  const keyBlocked = sendingKey && encryption !== 'ok'
  const idp = provider ? `prov-${provider.id.slice(0, 8)}` : 'prov-new'

  const pickPreset = (id) => {
    const next = presets.find((p) => p.id === id)
    const wasPresetName = !draft.name.trim() || presets.some((p) => p.label === draft.name)
    const wasPresetUrl = !draft.base_url.trim() || presets.some((p) => p.base_url && p.base_url === draft.base_url)
    set({
      preset: id,
      name: wasPresetName && next.id !== 'custom' ? next.label : draft.name,
      base_url: wasPresetUrl && next.base_url ? next.base_url : draft.base_url,
    })
  }

  const runTest = async () => {
    setBusy(true)
    try {
      const body = { base_url: draft.base_url.trim(), headers: headersOf(draft), name: draft.name.trim(), preset: draft.preset }
      if (sendingKey) body.api_key = draft.apiKey.trim()
      if (provider && draft.keyMode === 'keep') body.provider_id = provider.id
      const res = await testProvider(body)
      setTest(res)
      if (res.ok) setModels(res.models)
    } catch (err) {
      setTest({ ok: false, message: err.message })
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    const body = {
      name: draft.name.trim(),
      preset: draft.preset,
      base_url: draft.base_url.trim(),
      headers: headersOf(draft),
      default_model: draft.default_model.trim() || null,
    }
    if (sendingKey) body.api_key = draft.apiKey.trim()
    if (provider && draft.keyMode === 'clear') body.clear_api_key = true
    try {
      if (provider) {
        await updateProvider(provider.id, body)
        forgetModels(provider.id)
        toast.success(`Saved ${body.name}`)
      } else {
        await createProvider({ ...body, make_default: draft.make_default })
        toast.success(`Added ${body.name}`)
      }
      setDraft((d) => ({ ...d, apiKey: '' }))
      onDone()
    } catch (err) {
      toast.error('Could not save the provider', { description: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="r2a-rise space-y-4 rounded-lg border border-border bg-paper p-4" data-testid="provider-editor">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-preset`} className="text-[13px] font-semibold">Type</Label>
          <select id={`${idp}-preset`} data-testid="provider-preset-select" value={draft.preset} onChange={(e) => pickPreset(e.target.value)}
            className="h-9 w-full rounded-md border border-input bg-card px-2.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:text-[13px]">
            {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <p className="text-xs text-muted-foreground" data-testid="provider-preset-hint">{preset?.hint}</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idp}-name`} className="text-[13px] font-semibold">Name</Label>
          <Input id={`${idp}-name`} data-testid="provider-name-input" className={cn(fieldCls, 'font-sans')} value={draft.name} aria-invalid={Boolean(errors.name)} onChange={(e) => set({ name: e.target.value })} maxLength={60} />
          {errors.name && <p className="text-xs font-medium text-coral">{errors.name}</p>}
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${idp}-url`} className="text-[13px] font-semibold">Base URL</Label>
        <Input id={`${idp}-url`} data-testid="provider-url-input" className={fieldCls} value={draft.base_url} aria-invalid={Boolean(errors.base_url)} onChange={(e) => set({ base_url: e.target.value })} placeholder="https://api.example.com/v1" />
        {errors.base_url
          ? <p className="text-xs font-medium text-coral">{errors.base_url}</p>
          : <p className="text-xs text-muted-foreground">The OpenAI-compatible base, usually ending in /v1. Requests go to {'{base}'}/chat/completions and {'{base}'}/models.</p>}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${idp}-key`} className="flex items-center gap-1.5 text-[13px] font-semibold"><KeyRound className="size-3.5" /> API key <span className="font-normal text-muted-foreground">{preset?.needs_key ? '(required by this provider)' : '(optional)'}</span></Label>
        {provider && draft.keyMode !== 'replace' ? (
          <div className="flex flex-wrap items-center gap-2 text-[13px]" data-testid="provider-key-state">
            {draft.keyMode === 'keep'
              ? <span className="rounded-md border border-border bg-card px-2.5 py-1 font-mono text-xs">Key saved (hidden)</span>
              : <span className="rounded-md border border-coral/40 bg-coral/5 px-2.5 py-1 text-xs text-coral">The saved key will be removed</span>}
            <Button size="sm" variant="outline" onClick={() => set({ keyMode: 'replace', apiKey: '' })} data-testid="provider-key-replace">Replace</Button>
            {draft.keyMode === 'keep'
              ? <Button size="sm" variant="ghost" onClick={() => set({ keyMode: 'clear' })} data-testid="provider-key-clear">Remove</Button>
              : <Button size="sm" variant="ghost" onClick={() => set({ keyMode: 'keep' })}>Keep it</Button>}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Input id={`${idp}-key`} data-testid="provider-key-input" type="password" autoComplete="new-password" spellCheck={false} className={fieldCls}
              value={draft.apiKey} onChange={(e) => set({ apiKey: e.target.value })} placeholder={provider?.api_key_set ? 'New key' : 'sk-...'} />
            {provider?.api_key_set && <Button size="sm" variant="ghost" onClick={() => set({ keyMode: 'keep', apiKey: '' })}>Keep saved key</Button>}
          </div>
        )}
        <p className="text-xs text-muted-foreground">Encrypted with R2A_SECRET_KEY and never shown again; it is only sent to this provider's host.</p>
        {keyBlocked && (
          <p role="alert" className="flex items-center gap-1.5 text-xs font-medium text-coral" data-testid="provider-key-blocked"><LockKeyhole className="size-3.5" /> R2A_SECRET_KEY is {encryption === 'invalid' ? 'invalid' : 'not set'} - a key cannot be saved until it is fixed.</p>
        )}
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-semibold">Extra headers <span className="font-normal text-muted-foreground">(optional, not secret)</span></span>
          <Button size="xs" variant="ghost" onClick={() => set({ headers: [...draft.headers, { k: '', v: '' }] })} disabled={draft.headers.length >= 10} data-testid="provider-header-add"><Plus /> Add header</Button>
        </div>
        {draft.headers.map((h, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <div key={i} className="space-y-1">
            <div className="flex items-center gap-2">
              <Input aria-label="Header name" data-testid="provider-header-name" className={cn(fieldCls, 'w-2/5')} value={h.k} placeholder="X-Title" aria-invalid={Boolean(errors[`h${i}`])}
                onChange={(e) => set({ headers: draft.headers.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)) })} />
              <Input aria-label="Header value" data-testid="provider-header-value" className={fieldCls} value={h.v} placeholder="ROADMAP2ARENA"
                onChange={(e) => set({ headers: draft.headers.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)) })} />
              <Button size="icon" variant="ghost" aria-label="Remove header" onClick={() => set({ headers: draft.headers.filter((_, j) => j !== i) })}><X /></Button>
            </div>
            {errors[`h${i}`] && <p className="text-xs font-medium text-coral">{errors[`h${i}`]}</p>}
          </div>
        ))}
        <p className="text-xs text-muted-foreground">Authorization and cookie headers are not allowed here - use the API key field.</p>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor={`${idp}-model`} className="text-[13px] font-semibold">Default model <span className="font-normal text-muted-foreground">(optional)</span></Label>
          {provider && (
            <Button size="xs" variant="ghost" data-testid="provider-fetch-models" disabled={busy}
              onClick={() => loadModels(provider.id, { force: true }).then(setModels).catch((err) => toast.error('Could not fetch models', { description: `${err.message} - type the model name instead.` }))}>
              <RefreshCw /> Fetch models
            </Button>
          )}
        </div>
        <Input id={`${idp}-model`} data-testid="provider-model-input" className={fieldCls} value={draft.default_model} list={models?.length ? `${idp}-models` : undefined} autoComplete="off"
          onChange={(e) => set({ default_model: e.target.value })} placeholder="Pre-filled in the Create job form" />
        {models?.length > 0 && <datalist id={`${idp}-models`}>{models.map((m) => <option key={m} value={m} />)}</datalist>}
        {models && <p className="text-xs text-muted-foreground">{models.length} model{models.length === 1 ? '' : 's'} listed - pick one or type any name.</p>}
      </div>
      {!provider && (
        <label className="flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={draft.make_default} onChange={(e) => set({ make_default: e.target.checked })} data-testid="provider-make-default" className="size-4 accent-foreground" />
          Make this the default provider
        </label>
      )}
      {test && (
        <p role="status" data-testid="provider-test-result" className={cn('flex items-start gap-1.5 rounded-md border px-3 py-2 text-xs', test.ok ? 'border-teal/40 bg-teal/5 text-teal' : 'border-coral/40 bg-coral/5 text-coral')}>
          {test.ok ? <PlugZap className="mt-0.5 size-3.5 shrink-0" /> : <CircleAlert className="mt-0.5 size-3.5 shrink-0" />}
          <span>{test.ok ? `Connected in ${test.latency_ms} ms - ${test.model_count} model${test.model_count === 1 ? '' : 's'} listed.` : test.message}</span>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button onClick={save} disabled={busy || invalid || keyBlocked} data-testid="provider-save"><Save /> {provider ? 'Save provider' : 'Add provider'}</Button>
        <Button variant="outline" onClick={runTest} disabled={busy || Boolean(errors.base_url) || keyBlocked} data-testid="provider-test"><PlugZap /> Test connection</Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}><X /> Cancel</Button>
      </div>
    </div>
  )
}

// Settings > Providers: named OpenAI-compatible endpoints (contracts.md "Providers").
export default function ProvidersSettings() {
  const { data, error, refresh } = useProviders()
  const [editing, setEditing] = useState(null) // provider id, 'new' or null
  const [busyId, setBusyId] = useState(null)

  if (!data) {
    return (
      <section className="space-y-3 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6" data-testid="providers-settings">
        {error ? <p className="text-sm text-coral">Could not load providers: {error}</p> : [0, 1].map((i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
      </section>
    )
  }

  const act = async (id, fn, ok) => {
    setBusyId(id)
    try {
      await fn()
      if (ok) toast.success(ok)
      await refresh()
    } catch (err) {
      toast.error('Provider action failed', { description: err.message })
    } finally {
      setBusyId(null)
    }
  }
  const done = () => { setEditing(null); refresh() }

  return (
    <section className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6" data-testid="providers-settings">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-[15px] font-semibold"><Server className="size-4" /> Providers</h3>
          <p className="text-[13px] text-muted-foreground">OpenAI-compatible APIs jobs can run against: OpenAI, OpenRouter, Groq, Ollama, LM Studio, arena2api or any custom endpoint. The default is preselected in the Create job form.</p>
        </div>
        {editing !== 'new' && <Button size="sm" onClick={() => setEditing('new')} data-testid="provider-add"><Plus /> Add provider</Button>}
      </div>
      {data.encryption !== 'ok' && (
        <div role="alert" className="space-y-1.5 rounded-lg border border-coral/40 bg-coral/5 p-3 text-xs" data-testid="providers-key-missing">
          <p className="flex items-center gap-1.5 font-semibold text-coral"><LockKeyhole className="size-3.5" /> {data.encryption === 'invalid' ? 'R2A_SECRET_KEY is not a valid Fernet key' : 'R2A_SECRET_KEY is not set'}</p>
          <p>Providers without an API key work; to save keys, add R2A_SECRET_KEY to backend/.env and restart the backend. Generate one with:</p>
          <code className="block overflow-x-auto whitespace-nowrap rounded bg-muted px-2 py-1">{KEY_CMD}</code>
        </div>
      )}
      {editing === 'new' && <ProviderEditor presets={data.presets} encryption={data.encryption} onDone={done} onCancel={() => setEditing(null)} />}
      {data.providers.length === 0 && editing !== 'new' && (
        <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-[13px] text-muted-foreground" data-testid="providers-empty">No providers yet. Add one to create jobs.</p>
      )}
      <ul className="space-y-2">
        {data.providers.map((p) => (
          <li key={p.id} className="rounded-lg border border-border bg-background" data-testid="provider-row">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                  <span data-testid="provider-row-name">{p.name}</span>
                  {p.is_default && <span className="inline-flex items-center gap-1 rounded-full bg-teal/10 px-2 py-0.5 text-[11px] font-semibold text-teal" data-testid="provider-default-badge"><Star className="size-3 fill-current" /> Default</span>}
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{data.presets.find((x) => x.id === p.preset)?.label || p.preset}</span>
                  {p.api_key_set && !p.api_key_error && <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground"><KeyRound className="size-3" /> key saved</span>}
                </p>
                <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{p.base_url}{p.default_model ? ` - ${p.default_model}` : ''}</p>
                {p.api_key_error && <p className="mt-1 flex items-center gap-1.5 text-xs font-medium text-coral" data-testid="provider-key-error"><CircleAlert className="size-3.5" /> {p.api_key_error}</p>}
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {!p.is_default && (
                  <Button size="sm" variant="ghost" disabled={busyId === p.id} onClick={() => act(p.id, () => setDefaultProvider(p.id), `${p.name} is now the default`)} data-testid="provider-set-default"><Star /> Make default</Button>
                )}
                <Button size="sm" variant="outline" onClick={() => setEditing(editing === p.id ? null : p.id)} data-testid="provider-edit"><Pencil /> Edit</Button>
                <ConfirmButton testId="provider-delete" busy={busyId === p.id} confirmLabel="Delete?"
                  onConfirm={() => act(p.id, async () => { await deleteProvider(p.id); forgetModels(p.id); if (editing === p.id) setEditing(null) }, `Deleted ${p.name}`)}>
                  <Trash2 /> Delete
                </ConfirmButton>
              </div>
            </div>
            {editing === p.id && (
              <div className="border-t border-border p-3">
                <ProviderEditor provider={p} presets={data.presets} encryption={data.encryption} onDone={done} onCancel={() => setEditing(null)} />
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">A provider used by a queued, paused or running job cannot be deleted. Finished jobs keep its name and URL in their history.</p>
    </section>
  )
}
