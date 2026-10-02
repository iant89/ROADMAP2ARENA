import { useEffect, useState } from 'react'
import { Bot, MessagesSquare, RefreshCw, User } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { getTranscript } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatusChip } from './status'

const FENCE_RE = /^```([^\n`]*)\n([\s\S]*?)^```[ \t]*$/gm

// Plain text keeps its line breaks; fenced blocks render as dark code blocks.
function RichText({ text }) {
  const parts = []
  let pos = 0
  for (const m of text.matchAll(FENCE_RE)) {
    if (m.index > pos) parts.push({ kind: 'text', value: text.slice(pos, m.index).replace(/^\n+|\n+$/g, '') })
    parts.push({ kind: 'code', info: m[1].trim(), value: m[2] })
    pos = m.index + m[0].length
  }
  if (pos < text.length) parts.push({ kind: 'text', value: text.slice(pos).replace(/^\n+|\n+$/g, '') })
  return parts.map((p, i) => (p.kind === 'text'
    ? p.value && <p key={i} className="text-[13px] leading-relaxed whitespace-pre-wrap break-words">{p.value}</p>
    : (
      <div key={i} className="my-2">
        {p.info && <div className="mb-1 font-mono text-[11px] font-semibold text-muted-foreground">{p.info}</div>}
        <pre className="overflow-x-auto rounded-lg bg-ink p-3 font-mono text-[12px] leading-relaxed text-ink-foreground">{p.value}</pre>
      </div>
    )))
}

function Turn({ role, children }) {
  const user = role === 'user'
  return (
    <div className={cn('flex gap-3', !user && 'flex-row-reverse')} data-testid={`turn-${role}`}>
      <span className={cn('mt-1 grid size-7 shrink-0 place-items-center rounded-full', user ? 'bg-secondary text-foreground' : 'bg-primary text-primary-foreground')}>
        {user ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
      </span>
      <div className={cn('min-w-0 flex-1 space-y-1 rounded-xl border px-4 py-3', user ? 'border-border bg-paper' : 'border-border bg-card shadow-sm')}>
        <div className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{user ? 'User' : 'Assistant'}</div>
        {children}
      </div>
    </div>
  )
}

// Whole conversation of a job (GET /transcript): one user/assistant pair per sent step.
// `version` changes when the job makes progress so the transcript refreshes.
export default function FullTranscript({ jobId, version }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    getTranscript(jobId)
      .then((t) => { if (alive) { setData(t); setError(null) } })
      .catch((err) => alive && setError(err.message))
    return () => { alive = false }
  }, [jobId, version, attempt])

  if (error && !data) {
    return (
      <div className="flex items-center gap-3 p-5 text-[13px] text-coral" data-testid="transcript-error">
        Could not load the transcript: {error}
        <Button size="sm" variant="outline" onClick={() => setAttempt((a) => a + 1)}><RefreshCw /> Retry</Button>
      </div>
    )
  }
  if (!data) return <div className="space-y-3 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div>
  if (!data.turns.length) {
    return (
      <div className="grid place-items-center p-12 text-center" data-testid="transcript-empty">
        <div>
          <MessagesSquare className="mx-auto size-9 text-slate" />
          <p className="mt-3 text-sm font-semibold">No conversation yet</p>
          <p className="mt-1.5 text-[13px] text-muted-foreground">This job has not sent any step to arena2api.</p>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-7 p-4 lg:p-6" data-testid="full-transcript">
      {data.project_context?.trim() && (
        <div className="rounded-xl border border-dashed border-input bg-paper px-4 py-3">
          <div className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Project context</div>
          <p className="mt-1 text-[13px] whitespace-pre-wrap">{data.project_context}</p>
        </div>
      )}
      {data.turns.map((t) => (
        <section key={t.step_index} className="space-y-3" data-testid={`transcript-turn-${t.step_index}`}>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="font-mono text-xs text-muted-foreground">Step {t.step_index}</span>
            <h3 className="text-[14px] font-semibold">{t.step_title}</h3>
            <StatusChip status={t.status} testId="transcript-step-status" className="h-5 px-2 text-[10px]" />
            {t.artifact_paths.length > 0 && <span className="text-xs text-teal">{t.artifact_paths.length} file{t.artifact_paths.length === 1 ? '' : 's'}</span>}
          </div>
          <Turn role="user"><RichText text={t.prompt || '(prompt not recorded)'} /></Turn>
          {t.response && <Turn role="assistant"><RichText text={t.response} /></Turn>}
          {!t.response && t.status === 'running' && <Turn role="assistant"><p className="text-[13px] font-medium text-amber">Waiting for response...</p></Turn>}
          {t.error && <p className="rounded-lg border border-coral/30 bg-coral-soft px-3 py-2 text-[13px] font-medium text-coral">{t.error}</p>}
        </section>
      ))}
    </div>
  )
}
