import { useState } from 'react'
import { Bell, BellOff, CheckCheck, CircleCheckBig, CircleX, Inbox, Square, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import ConfirmButton from './ConfirmButton'
import { relativeTime } from './status'

const EVENT = {
  job_done: { icon: CircleCheckBig, cls: 'text-teal', label: 'Completed' },
  job_failed: { icon: CircleX, cls: 'text-coral', label: 'Failed' },
  job_stopped: { icon: Square, cls: 'text-pause', label: 'Stopped' },
  queue_empty: { icon: Inbox, cls: 'text-queue', label: 'Queue empty' },
}

function deliveryNote(d) {
  const failed = Object.entries(d || {}).filter(([, v]) => !v.ok).map(([k]) => k)
  return failed.length ? `${failed.join(' + ')} delivery failed` : null
}

// Bell with unread count; opens a panel with the notification history (newest first).
// Clicking an item marks it read and opens its job (or the queue).
export default function NotificationBell({ notifications }) {
  const [open, setOpen] = useState(false)
  const { items, unreadCount, total, markRead, markAll, remove, clear, open: openTarget } = notifications
  const badge = unreadCount > 99 ? '99+' : String(unreadCount)

  return (
    <>
      <Button
        size="icon-sm"
        variant="ghost"
        className="relative shrink-0"
        onClick={() => setOpen(true)}
        aria-label={unreadCount ? `Notifications - ${unreadCount} unread` : 'Notifications'}
        data-testid="notif-bell"
      >
        <Bell />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 grid h-4 min-w-4 place-items-center rounded-full bg-coral px-1 font-mono text-[10px] leading-none text-white" data-testid="notif-unread-count">{badge}</span>
        )}
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="w-full gap-0 bg-background sm:max-w-md" data-testid="notif-panel">
          <SheetHeader className="border-b border-border bg-card px-5 py-4">
            <SheetTitle className="flex items-center gap-2"><Bell className="size-4" /> Notifications</SheetTitle>
            <SheetDescription>{unreadCount} unread of {total}. Job finished, failed and stopped, and queue empty.</SheetDescription>
            <div className="flex flex-wrap gap-2 pt-2">
              <Button size="xs" variant="outline" onClick={markAll} disabled={!unreadCount} data-testid="notif-mark-all"><CheckCheck /> Mark all read</Button>
              <ConfirmButton size="xs" onConfirm={clear} disabled={!total} testId="notif-clear" confirmLabel="Clear all?"><Trash2 /> Clear</ConfirmButton>
            </div>
          </SheetHeader>
          <ScrollArea className="min-h-0 flex-1">
            {!items.length ? (
              <div className="grid place-items-center p-10 text-center text-muted-foreground" data-testid="notif-empty">
                <BellOff className="size-7" />
                <p className="mt-2 text-sm">No notifications</p>
              </div>
            ) : (
              <ul className="divide-y divide-border" data-testid="notif-list">
                {items.map((n) => {
                  const meta = EVENT[n.event] ?? { icon: Bell, cls: 'text-muted-foreground', label: n.event }
                  const Icon = meta.icon
                  const warn = deliveryNote(n.deliveries)
                  return (
                    <li key={n.id} className={cn('group flex gap-3 px-4 py-3', !n.read && 'bg-card')} data-testid={`notif-item-${n.id}`} data-read={n.read ? 'true' : 'false'}>
                      <Icon className={cn('mt-0.5 size-4 shrink-0', meta.cls)} />
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => { if (!n.read) markRead(n.id); setOpen(false); openTarget(n) }}
                        data-testid="notif-open"
                      >
                        <p className={cn('text-[13px] leading-snug', !n.read && 'font-semibold')}>{n.message}</p>
                        <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                          {meta.label} - {relativeTime(n.created_at)}
                          {warn && <span className="text-coral"> - {warn}</span>}
                        </p>
                      </button>
                      <div className="flex shrink-0 items-start gap-0.5">
                        {!n.read && (
                          <Button size="icon-xs" variant="ghost" onClick={() => markRead(n.id)} aria-label="Mark as read" title="Mark as read" data-testid="notif-mark-read">
                            <span className="size-2 rounded-full bg-coral" />
                          </Button>
                        )}
                        <Button size="icon-xs" variant="ghost" onClick={() => remove(n.id)} aria-label="Remove notification" title="Remove" data-testid="notif-remove"><X /></Button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </>
  )
}
