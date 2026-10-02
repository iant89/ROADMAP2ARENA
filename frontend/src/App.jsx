import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { ScrollArea } from '@/components/ui/scroll-area'
import HeaderBar from '@/components/r2a/HeaderBar'
import LeftPane from '@/components/r2a/LeftPane'
import WorkspaceTabs from '@/components/r2a/WorkspaceTabs'
import RecentJobsSheet from '@/components/r2a/RecentJobsSheet'
import { backendUrl, downloadZip, getConfig, getJob, parseRoadmap, restartJob, resumeJob, startJob, stopJob } from '@/lib/api'
import { useJob } from '@/hooks/useJob'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { SAMPLE_PROJECT_CONTEXT, SAMPLE_ROADMAP } from '@/constants/sampleRoadmap'
import BackendBanner from '@/components/r2a/BackendBanner'

const EMPTY_FORM = { arena_url: '', model: '', project_context: '', roadmap_md: '' }
const PARSE_DEBOUNCE_MS = 250

export default function App() {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [form, setForm] = useState(EMPTY_FORM)
  const [steps, setSteps] = useState([])
  const [activeJobId, setActiveJobId] = useState(null)
  const { job, error: jobError, refresh, restartPolling } = useJob(activeJobId)
  const [controlsBusy, setControlsBusy] = useState(false)
  const [configError, setConfigError] = useState(null)
  const [configAttempt, setConfigAttempt] = useState(0)
  const [parseError, setParseError] = useState(null)
  const [starting, setStarting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [formExpanded, setFormExpanded] = useState(false)
  const [recentOpen, setRecentOpen] = useState(false)
  const [tab, setTab] = useState('artifacts')
  const [selectedPath, setSelectedPath] = useState(null)
  const jobRunning = job?.status === 'running'

  // Defaults come from GET /api/config; only fill fields the user has not typed in.
  useEffect(() => {
    let alive = true
    getConfig()
      .then((cfg) => {
        if (!alive) return
        setConfigError(null)
        setForm((f) => ({ ...f, arena_url: f.arena_url || cfg.arena_url, model: f.model || cfg.model }))
      })
      .catch((err) => alive && setConfigError(err.message))
    return () => { alive = false }
  }, [configAttempt])

  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      parseRoadmap(form.roadmap_md)
        .then((res) => { if (alive) { setSteps(res.steps); setParseError(null) } })
        .catch((err) => alive && setParseError(err.message))
    }, PARSE_DEBOUNCE_MS)
    return () => { alive = false; clearTimeout(t) }
  }, [form.roadmap_md, configAttempt])

  // Keep a file selected: follow the newest artifact while running.
  useEffect(() => {
    if (!job?.artifacts.length) return
    const exists = job.artifacts.some((a) => a.path === selectedPath)
    if (!exists || (jobRunning && selectedPath === null)) {
      const latest = [...job.artifacts].sort((a, b) => b.step_index - a.step_index)[0]
      setSelectedPath(latest.path)
    }
  }, [job, jobRunning, selectedPath])

  // Deep link: /?job=<id> reopens that job on load.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('job')
    if (id) handlePickJob(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleStart = async () => {
    setStarting(true)
    try {
      const { id } = await startJob(form)
      setSelectedPath(null)
      setFormExpanded(false)
      setActiveJobId(id)
      toast.success('Job started', { description: `${steps.length} steps queued for ${form.model}` })
    } catch (err) {
      toast.error('Could not start job', { description: err.message })
    } finally {
      setStarting(false)
    }
  }

  const handleLoadSample = () => {
    setForm((f) => ({ ...f, project_context: SAMPLE_PROJECT_CONTEXT, roadmap_md: SAMPLE_ROADMAP }))
    toast('Sample roadmap loaded', { description: 'Tasky - a FastAPI todo API in 7 steps' })
  }

  const handlePickJob = async (id) => {
    try {
      const picked = await getJob(id)
      setForm({
        arena_url: picked.config.arena_url,
        model: picked.config.model,
        project_context: picked.project_context,
        roadmap_md: picked.roadmap_md,
      })
      setSelectedPath(null)
      setFormExpanded(false)
      setActiveJobId(id)
      setRecentOpen(false)
    } catch (err) {
      toast.error('Could not open job', { description: err.message })
    }
  }

  const applyOverridesToForm = (overrides) => {
    if (!overrides) return
    setForm((f) => ({ ...f, arena_url: overrides.arena_url.trim() || f.arena_url, model: overrides.model.trim() || f.model }))
  }

  const handleStop = async () => {
    if (!job) return
    setControlsBusy(true)
    try {
      const res = await stopJob(job.id)
      toast(`Job stopped${res.stopped_step ? ` at step ${res.stopped_step}` : ''}`, { description: `${res.steps_done} of ${job.step_total} steps finished. Resume or Restart when ready.` })
    } catch (err) {
      toast.error('Could not stop job', { description: err.message })
    } finally {
      setControlsBusy(false)
      refresh()
    }
  }

  const handleResume = async (overrides) => {
    if (!job) return
    setControlsBusy(true)
    try {
      const res = await resumeJob(job.id, overrides)
      applyOverridesToForm(overrides)
      toast.success(`Resumed from step ${res.resumed_from_step}`, { description: 'Earlier steps are replayed as conversation history.' })
      restartPolling()
    } catch (err) {
      toast.error('Could not resume job', { description: err.message })
    } finally {
      setControlsBusy(false)
    }
  }

  const handleRestart = async (overrides) => {
    if (!job) return
    setControlsBusy(true)
    try {
      const { id } = await restartJob(job.id, overrides)
      applyOverridesToForm(overrides)
      setSelectedPath(null)
      setActiveJobId(id)
      toast.success('Restarted as a new job', { description: `New job ${id.slice(0, 8)} runs all ${job.step_total} steps from the beginning.` })
    } catch (err) {
      toast.error('Could not restart job', { description: err.message })
    } finally {
      setControlsBusy(false)
    }
  }

  const handleDownload = useCallback(async () => {
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

  const left = (
    <LeftPane
      form={form}
      setForm={setForm}
      steps={steps}
      job={job}
      jobRunning={jobRunning}
      starting={starting}
      onStart={handleStart}
      onLoadSample={handleLoadSample}
      formExpanded={formExpanded}
      setFormExpanded={setFormExpanded}
      onClearJob={() => { setActiveJobId(null); setSelectedPath(null) }}
      onDownload={handleDownload}
      controlsBusy={controlsBusy}
      onStop={handleStop}
      onResume={handleResume}
      onRestart={handleRestart}
    />
  )
  const right = <WorkspaceTabs job={job} tab={tab} setTab={setTab} selectedPath={selectedPath} setSelectedPath={setSelectedPath} />

  return (
    <TooltipProvider delayDuration={200}>
      <div className={isDesktop ? 'flex h-dvh flex-col' : 'flex min-h-dvh flex-col'}>
        <HeaderBar job={job} onDownload={handleDownload} downloading={downloading} onOpenRecent={() => setRecentOpen(true)} />
        <BackendBanner
          message={configError || parseError || jobError}
          url={backendUrl}
          onRetry={() => { setConfigAttempt((a) => a + 1); refresh() }}
        />
        {isDesktop ? (
          <main className="min-h-0 flex-1">
            <ResizablePanelGroup orientation="horizontal" className="h-full">
              <ResizablePanel defaultSize="40%" minSize="28%" maxSize="60%" className="min-h-0">
                <ScrollArea className="h-full">{left}</ScrollArea>
              </ResizablePanel>
              <ResizableHandle withHandle className="bg-border hover:bg-input" />
              <ResizablePanel defaultSize="60%" minSize="35%" className="min-h-0 p-4 pl-3">
                <section className="h-full overflow-hidden rounded-xl border border-border bg-card shadow-sm">{right}</section>
              </ResizablePanel>
            </ResizablePanelGroup>
          </main>
        ) : (
          <main className="flex flex-col gap-2">
            {left}
            <section className="mx-3 mb-6 h-[78vh] overflow-hidden rounded-xl border border-border bg-card shadow-sm">{right}</section>
          </main>
        )}
        <RecentJobsSheet open={recentOpen} onOpenChange={setRecentOpen} onPick={handlePickJob} activeId={activeJobId} />
        <Toaster theme="light" position="bottom-right" richColors={false} />
      </div>
    </TooltipProvider>
  )
}
