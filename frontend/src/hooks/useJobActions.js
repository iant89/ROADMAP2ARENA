import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { downloadTranscriptHtml, downloadZip, restartJob, resumeJob, stopJob } from '@/lib/api'

// Stop / Resume / Restart / ZIP / transcript export for one job view.
export function useJobActions(job, { refresh, restartPolling, onOpenJob, onQueueChanged }) {
  const [busy, setBusy] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [exporting, setExporting] = useState(false)

  const act = async (fn) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false); onQueueChanged?.() }
  }

  const stop = () => act(async () => {
    try {
      const res = await stopJob(job.id)
      toast(`Job stopped${res.stopped_step ? ` at step ${res.stopped_step}` : ''}`, { description: `${res.steps_done} of ${job.step_total} steps finished. Resume or Restart when ready.` })
    } catch (err) {
      toast.error('Could not stop job', { description: err.message })
    } finally { refresh() }
  })

  const resume = (overrides) => act(async () => {
    try {
      const res = await resumeJob(job.id, overrides)
      toast.success(res.status === 'running' ? `Resumed from step ${res.resumed_from_step}` : `Resume queued at position ${res.queue_position}`,
        { description: 'Earlier steps are replayed as conversation history.' })
      restartPolling()
    } catch (err) {
      toast.error('Could not resume job', { description: err.message })
    }
  })

  const restart = (overrides) => act(async () => {
    try {
      const res = await restartJob(job.id, overrides)
      toast.success(res.status === 'running' ? 'Restarted as a new job' : `Restart queued at position ${res.queue_position}`,
        { description: `New job ${res.id.slice(0, 8)} runs all ${job.step_total} steps from the beginning.` })
      onOpenJob?.(res.id, res.status)
    } catch (err) {
      toast.error('Could not restart job', { description: err.message })
    }
  })

  const download = useCallback(async () => {
    if (!job) return
    setDownloading(true)
    try {
      const res = await downloadZip(job.id)
      toast.success('ZIP downloaded', { description: `${res.filename} (${Math.max(1, Math.round(res.size / 1024))} KB) - skipped paths are listed in the Log` })
    } catch (err) {
      toast.error('ZIP failed', { description: err.message })
    } finally {
      setDownloading(false)
      refresh()
    }
  }, [job, refresh])

  const exportTranscript = async () => {
    setExporting(true)
    try {
      const res = await downloadTranscriptHtml(job.id)
      toast.success('Transcript exported', { description: `${res.filename} - a standalone HTML file you can open offline` })
    } catch (err) {
      toast.error('Transcript export failed', { description: err.message })
    } finally {
      setExporting(false)
    }
  }

  return { busy, downloading, exporting, stop, resume, restart, download, exportTranscript }
}
