import { useEffect, useState } from 'react'
import { ArrowRight, History } from 'lucide-react'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { listJobs } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatusChip, formatDateTime } from './status'

export default function RecentJobsSheet({ open, onOpenChange, onPick, activeId }) {
  const [jobs, setJobs] = useState(null)

  useEffect(() => {
    if (!open) return
    let alive = true
    listJobs().then((list) => alive && setJobs(list))
    return () => { alive = false }
  }, [open])

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 bg-background sm:max-w-md" data-testid="recent-jobs-sheet">
        <SheetHeader className="border-b border-border bg-card px-5 py-4">
          <SheetTitle className="flex items-center gap-2"><History className="size-4" /> Recent jobs</SheetTitle>
          <SheetDescription>Stored in this browser (last 20). Pick one to reopen its full state.</SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-2.5 p-4">
            {jobs === null && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
            {jobs?.length === 0 && <p className="p-4 text-sm text-muted-foreground">No jobs yet.</p>}
            {jobs?.map((j) => (
              <button
                key={j.id}
                type="button"
                data-testid={`recent-job-${j.id}`}
                onClick={() => onPick(j.id)}
                className={cn(
                  'group w-full rounded-lg border bg-card p-3.5 text-left hover:-translate-y-px hover:border-input hover:shadow-md',
                  'transition-[transform,box-shadow,border-color] duration-200',
                  j.id === activeId ? 'border-primary ring-1 ring-primary' : 'border-border',
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2">
                    <StatusChip status={j.status} />
                    {j.is_sample && <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">Sample</span>}
                  </span>
                  <span className="text-xs text-muted-foreground">{formatDateTime(j.created_at)}</span>
                </div>
                <p className="mt-2 truncate text-[13.5px] font-medium">{j.title}</p>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono">{j.model}</span>
                  <span>-</span>
                  <span>{j.steps_done}/{j.step_total} steps</span>
                  {j.failed_step && <span className="text-coral">- failed at step {j.failed_step}</span>}
                  <ArrowRight className="ml-auto size-4 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5" />
                </div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
