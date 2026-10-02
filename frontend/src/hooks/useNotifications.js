import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { clearNotifications, deleteNotification, getNotifications, markAllNotificationsRead, markNotificationRead } from '@/lib/api'
import { showBrowser } from '@/lib/browserNotify'

const POLL_MS = 4000
const TOAST = {
  job_done: (n, o) => toast.success(n.message, o),
  job_failed: (n, o) => toast.error(n.message, o),
  job_stopped: (n, o) => toast.warning(n.message, o),
  queue_empty: (n, o) => toast.info(n.message, o),
}

// Polls GET /api/notifications. Notifications that appear after the first load are shown as
// toasts (and as browser notifications when enabled and the tab is not focused).
export function useNotifications({ onOpenJob, onOpenQueue }) {
  const [data, setData] = useState({ items: [], unread_count: 0, total: 0 })
  const seen = useRef(null)
  const handlers = useRef({ onOpenJob, onOpenQueue })
  useEffect(() => { handlers.current = { onOpenJob, onOpenQueue } }, [onOpenJob, onOpenQueue])

  const open = useCallback((n) => {
    if (n.job_id) handlers.current.onOpenJob?.(n.job_id)
    else handlers.current.onOpenQueue?.()
  }, [])

  const refresh = useCallback(async () => {
    try {
      const next = await getNotifications({ limit: 50 })
      setData(next)
      if (seen.current === null) {
        seen.current = new Set(next.items.map((n) => n.id))
        return
      }
      const fresh = next.items.filter((n) => !seen.current.has(n.id)).reverse()
      fresh.forEach((n) => seen.current.add(n.id))
      for (const n of fresh.filter((x) => !x.read)) {
        const show = TOAST[n.event] ?? ((x, o) => toast(x.message, o))
        show(n, { id: `notif-${n.id}`, action: { label: n.job_id ? 'Open' : 'View queue', onClick: () => open(n) } })
        showBrowser(n, () => open(n))
      }
    } catch {
      // the backend banner reports connectivity; keep the last list
    }
  }, [open])

  useEffect(() => {
    let alive = true
    let timer
    const tick = async () => {
      await refresh()
      if (alive) timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, [refresh])

  const act = useCallback(async (fn, optimistic) => {
    setData(optimistic)
    try { await fn() } catch (err) { toast.error('Notifications', { description: err.message }) }
    refresh()
  }, [refresh])

  return {
    items: data.items,
    unreadCount: data.unread_count,
    total: data.total,
    open,
    markRead: (id) => act(() => markNotificationRead(id), (d) => ({
      ...d, items: d.items.map((n) => (n.id === id ? { ...n, read: true } : n)),
      unread_count: Math.max(0, d.unread_count - (d.items.find((n) => n.id === id && !n.read) ? 1 : 0)),
    })),
    markAll: () => act(markAllNotificationsRead, (d) => ({ ...d, items: d.items.map((n) => ({ ...n, read: true })), unread_count: 0 })),
    remove: (id) => act(() => deleteNotification(id), (d) => ({
      ...d, items: d.items.filter((n) => n.id !== id), total: Math.max(0, d.total - 1),
      unread_count: Math.max(0, d.unread_count - (d.items.find((n) => n.id === id && !n.read) ? 1 : 0)),
    })),
    clear: () => act(clearNotifications, () => ({ items: [], unread_count: 0, total: 0 })),
  }
}
