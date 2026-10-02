import { useState } from 'react'
import { toast } from 'sonner'
import { Trash2 } from 'lucide-react'
import { bulkDeleteJobs, deleteFinishedJobs } from '@/lib/api'
import ConfirmButton from './ConfirmButton'

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function report(res) {
  const running = res.skipped.filter((s) => s.reason === 'running').length
  const msg = res.deleted_count ? `Deleted ${plural(res.deleted_count, 'job')}` : 'Nothing deleted'
  const description = running ? `${plural(running, 'running job')} skipped - stop it first.` : 'Steps and files removed.'
  if (res.deleted_count) toast.success(msg, { description })
  else toast.info(msg, { description })
}

// Selection-mode toolbar of the history panel: select all (deletable, visible), delete the
// selection, or delete every finished job. Both deletes use a two-click confirm.
export default function HistoryBulkBar({ visible, selected, setSelected, finishedCount, onDeleted }) {
  const [busy, setBusy] = useState(false)
  const selectable = visible.filter((j) => j.status !== 'running')
  const allChecked = selectable.length > 0 && selectable.every((j) => selected.has(j.id))

  const run = async (fn) => {
    setBusy(true)
    try {
      const res = await fn()
      report(res)
      setSelected(new Set())
      onDeleted(res.deleted)
    } catch (err) {
      toast.error('Could not delete jobs', { description: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-border bg-card px-2.5 py-2" data-testid="history-bulk-bar">
      <div className="flex items-center gap-2 text-[12px]">
        <label className="flex cursor-pointer items-center gap-1.5 font-medium">
          <input
            type="checkbox"
            className="size-3.5 accent-primary"
            checked={allChecked}
            disabled={!selectable.length}
            onChange={() => setSelected(allChecked ? new Set() : new Set(selectable.map((j) => j.id)))}
            data-testid="history-select-all"
          />
          Select all
        </label>
        <span className="ml-auto text-muted-foreground" data-testid="history-selected-count">{selected.size} selected</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <ConfirmButton
          size="xs"
          onConfirm={() => run(() => bulkDeleteJobs([...selected]))}
          disabled={!selected.size}
          busy={busy}
          testId="history-bulk-delete"
          confirmLabel={`Delete ${plural(selected.size, 'job')}?`}
        >
          <Trash2 /> Delete selected
        </ConfirmButton>
        <ConfirmButton
          size="xs"
          onConfirm={() => run(deleteFinishedJobs)}
          disabled={!finishedCount}
          busy={busy}
          testId="history-delete-finished"
          confirmLabel={`Delete ${plural(finishedCount, 'finished job')}?`}
          title="Deletes every completed, failed, stopped and cancelled job"
        >
          <Trash2 /> Delete all finished ({finishedCount})
        </ConfirmButton>
      </div>
    </div>
  )
}
