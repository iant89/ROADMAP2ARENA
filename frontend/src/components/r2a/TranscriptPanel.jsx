import { useEffect, useState } from 'react'
import { ChevronRight, MessagesSquare, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { getStep } from '@/lib/api'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { StatusIcon } from './status'

function Block({ label, text, tone }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{label}</div>
      <pre
        className={cn(
          'max-h-[420px] overflow-auto rounded-lg p-3.5 font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-words',
          tone === 'dark' ? 'bg-ink text-ink-foreground' : 'border border-border bg-paper text-foreground',
        )}
      >
        {text}
      </pre>
    </div>
  )
}

// Prompt and response are fetched on demand when an entry is expanded.
function useStepDetail(jobId, index, status, open) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!open) return undefined
    let alive = true
    setError(null)
    getStep(jobId, index)
      .then((d) => alive && setDetail(d))
      .catch((err) => alive && setError(err.message))
    return () => { alive = false }
  }, [jobId, index, status, open, attempt])
  return { detail, error, retry: () => setAttempt((a) => a + 1) }
}

function StepEntry({ jobId, step }) {
  const [open, setOpen] = useState(false)
  const { detail, error, retry } = useStepDetail(jobId, step.index, step.status, open)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border border-border bg-card" data-testid={`transcript-step-${step.index}`}>
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-lg px-4 py-3 text-left hover:bg-muted/60 transition-colors duration-150"
        >
          <ChevronRight className={cn('size-4 text-muted-foreground transition-transform duration-200', open && 'rotate-90')} />
          <StatusIcon status={step.status} />
          <span className="font-mono text-xs text-muted-foreground">Step {step.index}</span>
          <span className="min-w-0 flex-1 truncate text-[13.5px] font-medium">{step.title}</span>
          {step.files?.length > 0 && <span className="shrink-0 text-xs text-teal">{step.files.length} files</span>}
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="r2a-rise space-y-4 border-t border-border px-4 py-4">
        {error && (
          <div className="flex items-center gap-3 text-[13px] text-coral">
            Could not load step details: {error}
            <Button size="sm" variant="outline" onClick={retry}><RefreshCw /> Retry</Button>
          </div>
        )}
        {!detail && !error && <div className="space-y-2"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-24 w-full" /></div>}
        {detail && <Block label="Prompt" text={detail.prompt || '(not sent yet)'} />}
        {step.status === 'running' && <p className="text-[13px] font-medium text-amber">Waiting for response...</p>}
        {detail?.response && <Block label="Response" text={detail.response} tone="dark" />}
        {step.error && (
          <p className="rounded-lg border border-coral/30 bg-coral-soft px-3 py-2 text-[13px] font-medium text-coral">{step.error}</p>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

export default function TranscriptPanel({ job }) {
  const steps = (job?.steps ?? []).filter((s) => s.status !== 'pending')
  if (!steps.length) {
    return (
      <div className="grid h-full place-items-center p-10 text-center" data-testid="transcript-empty">
        <div>
          <MessagesSquare className="mx-auto size-9 text-slate" />
          <p className="mt-3 text-sm font-semibold">No transcript yet</p>
          <p className="mt-1.5 text-[13px] text-muted-foreground">Each step's prompt and full response will be listed here.</p>
        </div>
      </div>
    )
  }
  return (
    <ScrollArea className="h-full">
      <div className="space-y-2.5 p-4 lg:p-5" data-testid="transcript-panel">
        {steps.map((s) => <StepEntry key={`${job.id}-${s.index}`} jobId={job.id} step={s} />)}
      </div>
    </ScrollArea>
  )
}
