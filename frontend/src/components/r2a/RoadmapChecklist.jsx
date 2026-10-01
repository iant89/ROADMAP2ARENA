import { ListChecks, ListTodo } from 'lucide-react'
import { cn } from '@/lib/utils'
import { StatusIcon } from './status'

function EmptyHint() {
  return (
    <div className="rounded-xl border border-dashed border-input bg-paper p-5" data-testid="empty-roadmap-hint">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <ListTodo className="size-4 text-muted-foreground" /> No steps found
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
        Paste a ROADMAP.md above. Each <code className="rounded bg-muted px-1 py-0.5 text-[12px] text-foreground">### heading</code> becomes
        a step (its body is the description), and each unchecked
        <code className="mx-1 rounded bg-muted px-1 py-0.5 text-[12px] text-foreground">- [ ] item</code>
        outside a heading block is a standalone step. Other text and checked boxes are ignored.
      </p>
      <pre className="mt-3 overflow-x-auto rounded-lg bg-ink p-3 font-mono text-[12px] leading-relaxed text-ink-foreground">
{`### Project skeleton
Create pyproject.toml and app/main.py

- [ ] Add tests`}
      </pre>
    </div>
  )
}

export default function RoadmapChecklist({ steps, job }) {
  const list = job ? job.steps : steps
  const mode = job ? 'job' : 'preview'

  return (
    <section className="space-y-3" data-testid="roadmap-checklist">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-[13px] font-semibold tracking-wide text-muted-foreground uppercase">
          <ListChecks className="size-4" /> {mode === 'job' ? 'Roadmap progress' : 'Parsed steps'}
        </h2>
        {list.length > 0 && <span className="font-mono text-xs text-muted-foreground">{list.length} total</span>}
      </div>

      {list.length === 0 ? (
        <EmptyHint />
      ) : (
        <ol className="space-y-2">
          {list.map((step, i) => {
            const status = mode === 'job' ? step.status : 'pending'
            return (
              <li
                key={`${step.index}-${step.title}`}
                data-testid={`step-item-${step.index}`}
                data-status={status}
                style={{ animationDelay: `${Math.min(i, 10) * 35}ms` }}
                className={cn(
                  'r2a-rise group rounded-lg border bg-card px-3.5 py-3 hover:border-input hover:shadow-sm',
                  'transition-[border-color,box-shadow,background-color] duration-200',
                  status === 'running' && 'border-amber/40 bg-amber-soft/60 shadow-sm',
                  status === 'error' && 'border-coral/40 bg-coral-soft/60',
                  status === 'done' && 'border-border',
                  status === 'pending' && 'border-border',
                )}
              >
                <div className="flex items-start gap-3">
                  <span className="mt-0.5"><StatusIcon status={status} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-xs text-muted-foreground">{String(step.index).padStart(2, '0')}</span>
                      <span className={cn('text-[13.5px] font-medium', status === 'done' && 'text-foreground/80')}>{step.title}</span>
                    </div>
                    {step.description && (
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{step.description}</p>
                    )}
                    {status === 'running' && <p className="mt-1.5 text-xs font-medium text-amber">Waiting for arena2api response...</p>}
                    {status === 'error' && step.error && (
                      <p className="mt-1.5 text-xs font-medium text-coral" data-testid="step-error-message">{step.error}</p>
                    )}
                    {status === 'done' && step.files?.length > 0 && (
                      <p className="mt-1.5 text-xs text-teal">{step.files.length} file{step.files.length === 1 ? '' : 's'} generated</p>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
