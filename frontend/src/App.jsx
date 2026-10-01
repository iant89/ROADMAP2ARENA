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
import { downloadZip, getConfig, getJob, parseRoadmap, startJob } from '@/lib/api'
import { useJob } from '@/hooks/useJob'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { SAMPLE_PROJECT_CONTEXT, SAMPLE_ROADMAP } from '@/mock'

const EMPTY_FORM = { arena_url: '', model: '', step_delay_seconds: 2, project_context: '', roadmap_md: '' }

export default function App() {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [form, setForm] = useState(EMPTY_FORM)
  const [steps, setSteps] = useState([])
  const [activeJobId, setActiveJobId] = useState(null)
  const { job, refresh } = useJob(activeJobId)
  const [starting, setStarting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [formExpanded, setFormExpanded] = useState(false)
  const [recentOpen, setRecentOpen] = useState(false)
  const [tab, setTab] = useState('artifacts')
  const [selectedPath, setSelectedPath] = useState(null)
  const jobRunning = job?.status === 'running'

  useEffect(() => {
    getConfig().then((cfg) => setForm((f) => ({ ...f, ...cfg })))
  }, [])

  useEffect(() => {
    let alive = true
    parseRoadmap(form.roadmap_md).then((res) => alive && setSteps(res.steps))
    return () => { alive = false }
  }, [form.roadmap_md])

  // Keep a file selected: follow the newest artifact while running.
  useEffect(() => {
    if (!job?.artifacts.length) return
    const exists = job.artifacts.some((a) => a.path === selectedPath)
    if (!exists || (jobRunning && selectedPath === null)) {
      const latest = [...job.artifacts].sort((a, b) => b.step_index - a.step_index)[0]
      setSelectedPath(latest.path)
    }
  }, [job, jobRunning, selectedPath])

  const handleStart = async () => {
    setStarting(true)
    try {
      const { id } = await startJob(form)
      setSelectedPath(null)
      setFormExpanded(false)
      setActiveJobId(id)
      toast.success('Job started', { description: `${steps.length} steps queued for ${form.model} (simulated)` })
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
        step_delay_seconds: picked.config.step_delay_seconds,
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

  const handleDownload = useCallback(async () => {
    if (!job) return
    setDownloading(true)
    try {
      const res = await downloadZip(job.id)
      const extra = res.skipped.length ? `, ${res.skipped.length} skipped (see Log)` : ''
      toast.success('ZIP ready', { description: `${res.filename} - ${res.included.length} files${extra}` })
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
    />
  )
  const right = <WorkspaceTabs job={job} tab={tab} setTab={setTab} selectedPath={selectedPath} setSelectedPath={setSelectedPath} />

  return (
    <TooltipProvider delayDuration={200}>
      <div className={isDesktop ? 'flex h-dvh flex-col' : 'flex min-h-dvh flex-col'}>
        <HeaderBar job={job} onDownload={handleDownload} downloading={downloading} onOpenRecent={() => setRecentOpen(true)} />
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
