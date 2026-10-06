import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Copy, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { getCloneSource, parseRoadmap, startJob } from '@/lib/api'
import { useProviders } from '@/hooks/useProviders'
import StartForm from './StartForm'

// Form values for a clone: the source's provider when it still exists; a deleted provider falls
// back to the default provider (with a note); a legacy job keeps its own arena2api URL.
function formFrom(s, providerData) {
  const base = { model: s.model, project_id: s.project_id || '', project_context: s.project_context, roadmap_md: s.roadmap_md }
  if (!s.provider) return { ...base, provider_id: '', arena_url: s.arena_url }
  const exists = providerData?.providers.some((p) => p.id === s.provider.id)
  return { ...base, provider_id: exists ? s.provider.id : (providerData?.default_provider_id || ''), arena_url: '' }
}

const PARSE_DEBOUNCE_MS = 250

// "Clone job": the source job's inputs in an editable form; submitting enqueues a
// new job with cloned_from = source id.
export default function CloneJobSheet({ sourceId, open, onOpenChange, queue, settings, onCreated }) {
  const [source, setSource] = useState(null)
  const [form, setForm] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [stepCount, setStepCount] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const { data: providerData } = useProviders()
  const sourceForm = source && providerData ? formFrom(source, providerData) : null
  const providerGone = Boolean(source?.provider && providerData && !providerData.providers.some((p) => p.id === source.provider.id))

  useEffect(() => {
    if (!open || !sourceId) return undefined
    let alive = true
    setSource(null)
    setForm(null)
    setLoadError(null)
    getCloneSource(sourceId)
      .then((s) => {
        if (!alive) return
        setSource(s)
      })
      .catch((err) => alive && setLoadError(err.message))
    return () => { alive = false }
  }, [open, sourceId])

  // Fill the form once both the source job and the provider list are loaded.
  useEffect(() => {
    if (sourceForm && !form) setForm(sourceForm)
  }, [sourceForm, form])

  const roadmap = form?.roadmap_md
  useEffect(() => {
    if (roadmap === undefined) return undefined
    let alive = true
    const t = setTimeout(() => {
      parseRoadmap(roadmap).then((r) => alive && setStepCount(r.steps.length)).catch(() => alive && setStepCount(0))
    }, PARSE_DEBOUNCE_MS)
    return () => { alive = false; clearTimeout(t) }
  }, [roadmap])

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const res = await startJob({ ...form, cloned_from: sourceId })
      toast.success(res.status === 'running' ? 'Clone started' : `Clone added to the queue at position ${res.queue_position}`,
        { description: `${stepCount} steps for ${form.model} - clone of ${sourceId.slice(0, 8)}` })
      onOpenChange(false)
      onCreated(res)
    } catch (err) {
      toast.error('Could not add the clone', { description: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  const edited = sourceForm && form && ['provider_id', 'arena_url', 'model', 'project_id', 'project_context', 'roadmap_md']
    .some((k) => (form[k] ?? '') !== (sourceForm[k] ?? ''))

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 bg-background sm:max-w-2xl" data-testid="clone-job-sheet">
        <SheetHeader className="border-b border-border bg-card px-5 py-4">
          <SheetTitle className="flex items-center gap-2"><Copy className="size-4" /> Clone job</SheetTitle>
          <SheetDescription>
            Prefilled from {source?.title ? <strong className="text-foreground">{source.title}</strong> : 'the source job'}
            {sourceId && <> (<span className="font-mono">{sourceId.slice(0, 8)}</span>)</>}. Edit anything, then add it to the queue as a new job.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-5">
            {loadError && <p className="text-sm text-coral" data-testid="clone-load-error">Could not load the source job: {loadError}</p>}
            {!form && !loadError && [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
            {form && (
              <>
                {edited && (
                  <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-paper px-3.5 py-2 text-xs text-muted-foreground" data-testid="clone-edited-note">
                    Edited - differs from the source job.
                    <Button size="xs" variant="ghost" onClick={() => setForm(sourceForm)} data-testid="clone-reset-button">
                      <RotateCcw /> Reset to source
                    </Button>
                  </div>
                )}
                {providerGone && (
                  <p className="rounded-lg border border-amber/40 bg-amber-soft px-3.5 py-2 text-xs" data-testid="clone-provider-gone">
                    The source job used provider <strong>{source.provider.name}</strong>, which was deleted. Pick a provider for the clone.
                  </p>
                )}
                <StartForm
                  form={form}
                  onChange={setForm}
                  stepCount={stepCount}
                  onSubmit={handleSubmit}
                  submitting={submitting}
                  queueInfo={queue ? { running: Boolean(queue.running), waiting: queue.waiting } : null}
                  settings={settings}
                  submitLabel="Add clone to queue"
                  legacyUrl={source && !source.provider ? source.arena_url : null}
                />
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
