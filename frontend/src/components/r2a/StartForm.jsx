import { ChevronDown, ChevronUp, FileText, Play, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Collapsible, CollapsibleContent } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

function Field({ id, label, hint, children }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={id} className="text-[13px] font-semibold">{label}</Label>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

const inputCls = 'bg-card h-9'

export default function StartForm({
  form, onChange, stepCount, onStart, onLoadSample, starting, jobRunning, collapsed, expanded, onToggleExpanded,
}) {
  const set = (key) => (e) => onChange({ ...form, [key]: e.target.value })
  const startDisabled = jobRunning || starting || stepCount === 0 || !form.arena_url.trim() || !form.model.trim()
  const open = !collapsed || expanded

  const fields = (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-[1.4fr_1fr]">
        <Field id="arena_url" label="arena2api base URL">
          <Input id="arena_url" data-testid="arena-url-input" className={cn(inputCls, 'font-mono text-[13px]')} value={form.arena_url} onChange={set('arena_url')} placeholder="http://localhost:9090" />
        </Field>
        <Field id="model" label="Model">
          <Input id="model" data-testid="model-input" className={cn(inputCls, 'font-mono text-[13px]')} value={form.model} onChange={set('model')} placeholder="gpt-4o" />
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
          className="field-sizing-fixed h-52 resize-y bg-card font-mono text-[12.5px] leading-relaxed"
          value={form.roadmap_md}
          onChange={set('roadmap_md')}
          placeholder={'### Project skeleton\nCreate the package layout...\n\n- [ ] Add tests'}
          spellCheck={false}
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" onClick={onStart} disabled={startDisabled} data-testid="start-job-button" className="px-4 hover:-translate-y-px">
          <Play /> {jobRunning ? 'Job running...' : 'Start job'}
        </Button>
        <Button size="lg" variant="outline" onClick={onLoadSample} disabled={jobRunning} data-testid="load-sample-button" className="px-3 hover:-translate-y-px">
          <Sparkles /> Load sample roadmap
        </Button>
        {jobRunning && <span className="text-xs text-muted-foreground">One job at a time - wait for it to finish.</span>}
      </div>
    </div>
  )

  if (!collapsed) return fields

  return (
    <Collapsible open={open} onOpenChange={onToggleExpanded}>
      <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3.5 py-2.5" data-testid="form-summary">
        <FileText className="size-4 shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 truncate text-[13px]">
          <span className="font-mono font-medium">{form.model}</span>
          <span className="mx-2 text-muted-foreground">via</span>
          <span className="font-mono text-muted-foreground">{form.arena_url}</span>
          <span className="mx-2 text-muted-foreground">-</span>
          <span>{stepCount} steps</span>
        </p>
        <Button variant="ghost" size="sm" onClick={() => onToggleExpanded(!open)} data-testid="toggle-form-button">
          {open ? <ChevronUp /> : <ChevronDown />} {open ? 'Hide inputs' : 'Edit inputs'}
        </Button>
      </div>
      <CollapsibleContent className="r2a-rise pt-5">{fields}</CollapsibleContent>
    </Collapsible>
  )
}
