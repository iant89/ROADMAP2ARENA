import { useEffect, useState } from 'react'
import { CircleAlert, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cachedModels, loadModels } from '@/hooks/useProviders'
import { cn } from '@/lib/utils'
import { LEGACY, modelError, providerError, selectionOf } from '@/lib/providerForm'

function Hint({ id, error, children }) {
  if (error) {
    return (
      <p id={id} role="alert" data-testid={id} className="r2a-rise flex items-center gap-1.5 text-xs font-medium text-coral">
        <CircleAlert className="size-3.5 shrink-0" /> {error}
      </p>
    )
  }
  return children ? <p className="text-xs text-muted-foreground">{children}</p> : null
}

// Provider select + model input (free text, with a datalist of the provider's fetched models).
// value: { provider_id, arena_url, model }; onChange receives the next value.
// Fetching models is best effort: when GET {base}/models fails, the model is simply typed.
export default function ProviderModelFields({ value, onChange, providers, fallbackModel = '', legacyUrl = null, idPrefix = 'job', compact = false, showErrors = { provider: true, model: true } }) {
  const sel = selectionOf(value)
  const provider = providers?.find((p) => p.id === sel) ?? null
  const [models, setModels] = useState(() => (provider ? cachedModels(provider.id) : null))
  const [fetchState, setFetchState] = useState({ busy: false, error: null })

  const fetchFor = (id, force) => {
    setFetchState({ busy: true, error: null })
    loadModels(id, { force })
      .then((list) => { setModels(list); setFetchState({ busy: false, error: null }) })
      .catch((err) => { setModels(null); setFetchState({ busy: false, error: err.message }) })
  }

  // Load (or reuse) the model list whenever the selected provider changes.
  const providerId = provider?.id
  useEffect(() => {
    if (!providerId) { setModels(null); setFetchState({ busy: false, error: null }); return }
    const cached = cachedModels(providerId)
    if (cached) { setModels(cached); setFetchState({ busy: false, error: null }) } else fetchFor(providerId, false)
  }, [providerId])

  const selectProvider = (e) => {
    const next = e.target.value
    if (next === LEGACY) { onChange({ ...value, provider_id: '', arena_url: legacyUrl || value.arena_url }); return }
    const target = providers?.find((p) => p.id === next)
    // Swap the model only when it is still the previous provider's (or the global) default.
    const oldDefault = provider?.default_model || fallbackModel
    const keepModel = value.model.trim() && value.model !== oldDefault
    onChange({ ...value, provider_id: next, arena_url: '', model: keepModel ? value.model : (target?.default_model || fallbackModel || value.model) })
  }

  const pErr = providerError(value, providers)
  const mErr = modelError(value)
  const selectId = `${idPrefix}-provider`
  const modelId = `${idPrefix}-model`
  const listId = `${idPrefix}-model-options`
  const size = compact ? 'h-8 text-[12.5px]' : 'h-9 text-base sm:text-sm'
  const deleted = sel && sel !== LEGACY && providers && !provider

  let modelHint = null
  if (fetchState.busy) modelHint = 'Fetching models...'
  else if (fetchState.error) modelHint = `Could not fetch models (${fetchState.error}). Type the model name.`
  else if (models) modelHint = models.length ? `${models.length} model${models.length === 1 ? '' : 's'} available - pick one or type any name.` : 'The provider listed no models - type the model name.'
  else if (sel === LEGACY) modelHint = 'Legacy job: model as served by that arena2api URL.'

  return (
    <div className={cn('grid gap-4', compact ? 'gap-3 sm:grid-cols-[1.2fr_1fr]' : 'sm:grid-cols-[1.2fr_1fr]')}>
      <div className="space-y-1.5">
        <Label htmlFor={selectId} className={cn('font-semibold', compact ? 'text-xs' : 'text-[13px]')}>Provider</Label>
        <select
          id={selectId}
          data-testid={`${idPrefix}-provider-select`}
          aria-invalid={Boolean(showErrors.provider && pErr)}
          className={cn('w-full rounded-md border border-input bg-card px-2.5 font-mono shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive', size)}
          value={sel}
          onChange={selectProvider}
          disabled={!providers}
        >
          {!providers && <option value={sel}>Loading providers...</option>}
          {providers && !sel && <option value="">Choose a provider</option>}
          {deleted && <option value={sel}>(deleted provider)</option>}
          {providers?.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_default ? ' (default)' : ''}</option>)}
          {(legacyUrl || sel === LEGACY) && <option value={LEGACY}>Legacy arena2api URL</option>}
        </select>
        {sel === LEGACY ? (
          <Input
            aria-label="Legacy arena2api base URL"
            data-testid={`${idPrefix}-legacy-url-input`}
            className={cn('bg-card font-mono', size)}
            value={value.arena_url}
            onChange={(e) => onChange({ ...value, arena_url: e.target.value })}
          />
        ) : null}
        <Hint id={`${selectId}-error`} error={showErrors.provider ? pErr : null}>
          {provider && <span className="font-mono" data-testid={`${idPrefix}-provider-base`}>{provider.base_url}</span>}
        </Hint>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor={modelId} className={cn('font-semibold', compact ? 'text-xs' : 'text-[13px]')}>Model</Label>
          {provider && (
            <Button type="button" size="xs" variant="ghost" onClick={() => fetchFor(provider.id, true)} disabled={fetchState.busy} data-testid={`${idPrefix}-fetch-models`}>
              <RefreshCw className={cn(fetchState.busy && 'animate-spin')} /> Fetch models
            </Button>
          )}
        </div>
        <Input
          id={modelId}
          data-testid={idPrefix === 'job' ? 'model-input' : `${idPrefix}-model-input`}
          list={models?.length ? listId : undefined}
          autoComplete="off"
          aria-invalid={Boolean(showErrors.model && mErr)}
          className={cn('bg-card font-mono', size)}
          value={value.model}
          onChange={(e) => onChange({ ...value, model: e.target.value })}
          placeholder={provider?.default_model || fallbackModel || 'gpt-4o-mini'}
        />
        {models?.length > 0 && (
          <datalist id={listId} data-testid={`${idPrefix}-model-options`}>
            {models.map((m) => <option key={m} value={m} />)}
          </datalist>
        )}
        <Hint id={`${modelId}-error`} error={showErrors.model ? mErr : null}>{modelHint}</Hint>
      </div>
    </div>
  )
}
