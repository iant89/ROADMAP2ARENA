import { useEffect, useRef } from 'react'
import { ScrollText } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { formatTime } from './status'

const LEVEL_STYLE = {
  info: 'text-[#9FB3C8]',
  ok: 'text-[#5FD4BF]',
  warn: 'text-[#F2C266]',
  error: 'text-[#FF8F75]',
}

export default function LogPanel({ job }) {
  const wrapRef = useRef(null)
  const lines = job?.log ?? []

  useEffect(() => {
    const viewport = wrapRef.current?.querySelector('[data-slot="scroll-area-viewport"]')
    if (viewport) viewport.scrollTop = viewport.scrollHeight
  }, [lines.length, job?.id])

  if (!lines.length) {
    return (
      <div className="grid h-full place-items-center p-10 text-center" data-testid="log-empty">
        <div>
          <ScrollText className="mx-auto size-9 text-slate" />
          <p className="mt-3 text-sm font-semibold">Log is empty</p>
          <p className="mt-1.5 text-[13px] text-muted-foreground">Start a job to see timestamped activity.</p>
        </div>
      </div>
    )
  }

  return (
    <div ref={wrapRef} className="h-full min-h-0 bg-ink" data-testid="log-panel">
      <ScrollArea className="h-full">
        <div className="space-y-0.5 p-4 font-mono text-[12px] leading-relaxed">
          {lines.map((l, i) => (
            <div key={i} className="flex gap-3 rounded px-1.5 hover:bg-white/5">
              <span className="shrink-0 text-ink-foreground/45 tabular-nums">{formatTime(l.ts)}</span>
              <span className={cn('w-11 shrink-0 font-semibold uppercase', LEVEL_STYLE[l.level] || LEVEL_STYLE.info)}>{l.level}</span>
              <span className="min-w-0 break-words text-ink-foreground">{l.msg}</span>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
