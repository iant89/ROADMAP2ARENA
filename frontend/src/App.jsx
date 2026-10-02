import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Activity, History, ListOrdered, PlusCircle, Settings } from 'lucide-react'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import HeaderBar from '@/components/r2a/HeaderBar'
import BackendBanner from '@/components/r2a/BackendBanner'
import CreateJobTab from '@/components/r2a/CreateJobTab'
import QueueTab from '@/components/r2a/QueueTab'
import CurrentJobTab from '@/components/r2a/CurrentJobTab'
import HistoryTab from '@/components/r2a/HistoryTab'
import SettingsTab from '@/components/r2a/SettingsTab'
import { backendUrl, getJob, getSettings } from '@/lib/api'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useQueue } from '@/hooks/useQueue'
import { useUrlState } from '@/hooks/useUrlState'
import { cn } from '@/lib/utils'

const EMPTY_FORM = { arena_url: '', model: '', project_context: '', roadmap_md: '' }

function Count({ n, testId }) {
  if (!n) return null
  return <span data-testid={testId} className="rounded-full bg-queue px-1.5 font-mono text-[10.5px] leading-[18px] text-white">{n}</span>
}

export default function App() {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [{ tab, job: historyJobId }, navigate] = useUrlState()
  const { queue, error: queueError, refresh: refreshQueue } = useQueue()
  const [form, setForm] = useState(EMPTY_FORM)
  const [settings, setSettings] = useState(null)
  const [settingsError, setSettingsError] = useState(null)
  const [settingsAttempt, setSettingsAttempt] = useState(0)
  const [parseError, setParseError] = useState(null)
  const [finishedKey, setFinishedKey] = useState(0)
  const prevRunning = useRef(undefined)
  const runningId = queue?.running?.job_id ?? null

  // Form defaults come from the backend settings; only fill fields the user has not typed in.
  useEffect(() => {
    let alive = true
    getSettings()
      .then((s) => {
        if (!alive) return
        setSettings(s)
        setSettingsError(null)
        setForm((f) => ({ ...f, arena_url: f.arena_url || s.arena_url, model: f.model || s.model }))
      })
      .catch((err) => alive && setSettingsError(err.message))
    return () => { alive = false }
  }, [settingsAttempt])

  // Global notifications from the queue poll: the running job ended (finished/failed)
  // and the scheduler started the next job. Also refreshes the history list.
  useEffect(() => {
    if (!queue) return
    const prev = prevRunning.current
    if (prev && prev !== runningId) {
      setFinishedKey((k) => k + 1)
      getJob(prev).then((j) => {
        if (j.status === 'done') toast.success(`Job finished: ${j.title}`, { description: `${j.steps_done}/${j.step_total} steps done - ${j.artifacts.length} files ready for ZIP` })
        else if (j.status === 'error') toast.error(`Step ${j.failed_step ?? '?'} failed: ${j.title}`, { description: j.error })
      }).catch(() => {})
    }
    if (prev !== undefined && runningId && prev !== runningId) {
      toast(`Started: ${queue.running.title}`, { description: `${queue.running.step_total} steps with ${queue.running.model}` })
    }
    prevRunning.current = runningId
  }, [queue, runningId])

  const handleSettingsSaved = (next) => {
    setForm((f) => ({
      ...f,
      arena_url: !f.arena_url || f.arena_url === settings?.arena_url ? next.arena_url : f.arena_url,
      model: !f.model || f.model === settings?.model ? next.model : f.model,
    }))
    setSettings(next)
  }

  const handleCreated = (res, stepCount) => {
    refreshQueue()
    if (res.status === 'running') {
      toast.success('Job started', { description: `${stepCount} steps for ${form.model}` })
      navigate('current')
    } else {
      toast.success(`Added to the queue at position ${res.queue_position}`, {
        description: `${stepCount} steps for ${form.model} - starts when the jobs ahead finish.`,
        action: { label: 'View queue', onClick: () => navigate('queue') },
      })
    }
  }

  const openJob = useCallback((id, status) => {
    refreshQueue()
    if (status === 'running') navigate('current')
    else navigate('history', id)
  }, [navigate, refreshQueue])

  const handleCloned = (res) => {
    refreshQueue()
    setFinishedKey((k) => k + 1)
    if (res.status === 'running') navigate('current')
    else navigate('history', res.id)
  }

  const onParseError = useCallback((msg) => setParseError(msg), [])
  const queueCount = queue?.count ?? 0
  const scroll = (node) => (isDesktop ? <ScrollArea className="h-full">{node}</ScrollArea> : node)
  const contentCls = cn('min-h-0', isDesktop && 'h-full')

  return (
    <TooltipProvider delayDuration={200}>
      <div className={isDesktop ? 'flex h-dvh flex-col' : 'flex min-h-dvh flex-col'}>
        <HeaderBar running={queue?.running} queueCount={queueCount} onOpenCurrent={() => navigate('current')} onOpenQueue={() => navigate('queue')} />
        <BackendBanner
          message={settingsError || parseError || queueError}
          url={backendUrl}
          onRetry={() => { setSettingsAttempt((a) => a + 1); refreshQueue() }}
        />
        <Tabs value={tab} onValueChange={(t) => navigate(t)} className="flex min-h-0 flex-1 flex-col gap-0">
          <div className="overflow-x-auto border-b border-border bg-card/60 px-3 py-2 lg:px-7">
            <TabsList className="h-9 w-full bg-secondary sm:w-fit" data-testid="main-tabs">
              <TabsTrigger value="create" data-testid="tab-create" aria-label="Create job" className="flex-1 justify-center px-2 sm:px-3"><PlusCircle /><span className="hidden sm:inline">Create job</span></TabsTrigger>
              <TabsTrigger value="queue" data-testid="tab-queue" aria-label="Job queue" className="flex-1 justify-center px-2 sm:px-3"><ListOrdered /><span className="hidden sm:inline">Job queue</span> <Count n={queueCount} testId="queue-count-badge" /></TabsTrigger>
              <TabsTrigger value="current" data-testid="tab-current" aria-label="Current job" className="flex-1 justify-center px-2 sm:px-3">
                <Activity className={cn(runningId && 'text-amber')} /><span className="hidden sm:inline">Current job</span>
                {runningId && <span className="size-1.5 rounded-full bg-amber" aria-label="running" />}
              </TabsTrigger>
              <TabsTrigger value="history" data-testid="tab-history" aria-label="Job history" className="flex-1 justify-center px-2 sm:px-3"><History /><span className="hidden sm:inline">Job history</span></TabsTrigger>
              <TabsTrigger value="settings" data-testid="tab-settings" aria-label="Settings" className="flex-1 justify-center px-2 sm:px-3"><Settings /><span className="hidden sm:inline">Settings</span></TabsTrigger>
            </TabsList>
          </div>
          <main className={cn('min-h-0', isDesktop && 'flex-1')}>
            <TabsContent value="create" className={contentCls}>
              {scroll(
                <CreateJobTab form={form} setForm={setForm} settings={settings} queue={queue} onCreated={handleCreated}
                  onOpenSettings={() => navigate('settings')} onParseError={onParseError} />,
              )}
            </TabsContent>
            <TabsContent value="queue" className={contentCls}>
              {scroll(
                <QueueTab queue={queue} error={queueError} refresh={refreshQueue} onOpenCurrent={() => navigate('current')}
                  onOpenJob={(id) => navigate('history', id)} onCreate={() => navigate('create')} />,
              )}
            </TabsContent>
            <TabsContent value="current" className={contentCls}>
              <CurrentJobTab queue={queue} onOpenJob={openJob} onOpenQueue={() => navigate('queue')}
                onCreate={() => navigate('create')} onQueueChanged={refreshQueue} />
            </TabsContent>
            <TabsContent value="history" className={contentCls}>
              <HistoryTab jobId={historyJobId} onOpen={(id) => navigate('history', id)} onOpenJob={openJob}
                onOpenQueue={() => navigate('queue')} onQueueChanged={refreshQueue} refreshKey={finishedKey}
                isDesktop={isDesktop} queue={queue} settings={settings} onCloned={handleCloned} />
            </TabsContent>
            <TabsContent value="settings" className={contentCls}>
              {scroll(<SettingsTab onSaved={handleSettingsSaved} />)}
            </TabsContent>
          </main>
        </Tabs>
        <Toaster theme="light" position="bottom-right" richColors={false} />
      </div>
    </TooltipProvider>
  )
}
