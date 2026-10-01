import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export const STATUS_META = {
  pending: { label: 'Pending', icon: Circle, tone: 'text-slate', chip: 'bg-muted text-muted-foreground border-border' },
  running: { label: 'Running', icon: Loader2, tone: 'text-amber', chip: 'bg-amber-soft text-amber border-amber/30' },
  done: { label: 'Done', icon: CheckCircle2, tone: 'text-teal', chip: 'bg-teal-soft text-teal border-teal/30' },
  error: { label: 'Error', icon: XCircle, tone: 'text-coral', chip: 'bg-coral-soft text-coral border-coral/30' },
  idle: { label: 'Idle', icon: Circle, tone: 'text-slate', chip: 'bg-card text-muted-foreground border-border' },
}

export function StatusIcon({ status, className }) {
  const meta = STATUS_META[status] || STATUS_META.pending
  const Icon = meta.icon
  return (
    <Icon
      aria-label={meta.label}
      className={cn('size-4 shrink-0', meta.tone, status === 'running' && 'animate-spin', className)}
    />
  )
}

export function StatusChip({ status, children, className }) {
  const meta = STATUS_META[status] || STATUS_META.pending
  return (
    <span
      data-testid="job-status-badge"
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold tracking-wide uppercase',
        meta.chip,
        className,
      )}
    >
      <StatusIcon status={status} className="size-3.5" />
      {children || meta.label}
    </span>
  )
}

export function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

export function formatDateTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}
