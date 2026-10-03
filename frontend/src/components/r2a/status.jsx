import { Ban, CheckCircle2, Circle, CircleStop, ListOrdered, Loader2, Pause, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export const STATUS_META = {
  pending: { label: 'Pending', icon: Circle, tone: 'text-slate', chip: 'bg-muted text-muted-foreground border-border' },
  running: { label: 'Running', icon: Loader2, tone: 'text-amber', chip: 'bg-amber-soft text-amber border-amber/30' },
  done: { label: 'Completed', icon: CheckCircle2, tone: 'text-teal', chip: 'bg-teal-soft text-teal border-teal/30' },
  error: { label: 'Failed', icon: XCircle, tone: 'text-coral', chip: 'bg-coral-soft text-coral border-coral/30' },
  stopped: { label: 'Stopped', icon: CircleStop, tone: 'text-stop', chip: 'bg-stop-soft text-stop border-stop/30' },
  queued: { label: 'Queued', icon: ListOrdered, tone: 'text-queue', chip: 'bg-queue-soft text-queue border-queue/30' },
  paused: { label: 'Paused', icon: Pause, tone: 'text-pause', chip: 'bg-pause-soft text-pause border-pause/35 border-dashed' },
  cancelled: { label: 'Cancelled', icon: Ban, tone: 'text-slate', chip: 'bg-muted text-slate border-border' },
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

export const FINISHED_STATUSES = ['done', 'error', 'stopped', 'cancelled']

export function StatusChip({ status, children, className, testId = 'job-status-badge' }) {
  const meta = STATUS_META[status] || STATUS_META.pending
  return (
    <span
      data-testid={testId}
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

export function relativeTime(iso) {
  if (!iso) return ''
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return formatDateTime(iso)
}

// Provider name for a job: its provider snapshot, or "arena2api" for a legacy (pre-provider) job.
export const providerName = (provider) => provider?.name || 'arena2api'
