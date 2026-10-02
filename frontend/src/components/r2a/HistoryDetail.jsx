import { useEffect, useState } from 'react'
import { Copy, Download, FileDown, FolderTree, ListChecks, MessagesSquare, ScrollText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useJob } from '@/hooks/useJob'
import { useJobActions } from '@/hooks/useJobActions'
import { cn } from '@/lib/utils'
import ArtifactsPanel from './ArtifactsPanel'
import FullTranscript from './FullTranscript'
import JobAlerts from './JobAlerts'
import JobControls from './JobControls'
import LogPanel from './LogPanel'
import RoadmapChecklist from './RoadmapChecklist'
import { QueuedAlert } from './JobView'
import { StatusChip, formatDateTime } from './status'

function Count({ n }) {
  return <span className="rounded-full bg-secondary px-1.5 font-mono text-[10.5px] text-secondary-foreground">{n}</span>
}

function JobLink({ label, id, onOpen }) {
  return (
    <span>{label} <button type="button" className="font-mono underline underline-offset-2 hover:text-foreground" onClick={() => onOpen(id)}>{id.slice(0, 8)}</button></span>
  )
}

// Job history detail: header with actions (Clone, transcript export, ZIP, Resume/Restart/Stop)
// and Transcript | Files | Steps | Log tabs.
export default function HistoryDetail({ jobId, onOpenJob, onOpenQueue, onQueueChanged, onClone }) {
  const { job, error, refresh, restartPolling } = useJob(jobId)
  const actions = useJobActions(job, { refresh, restartPolling, onOpenJob, onQueueChanged })
  const [tab, setTab] = useState('transcript')
  const [selectedPath, setSelectedPath] = useState(null)

  useEffect(() => { setSelectedPath(null) }, [jobId])
  useEffect(() => {
    if (!job?.artifacts.length || job.artifacts.some((a) => a.path === selectedPath)) return
    setSelectedPath([...job.artifacts].sort((a, b) => b.step_index - a.step_index)[0].path)
  }, [job, selectedPath])

  if (!job) {
    return (
      <div className="space-y-3 p-6" data-testid="history-detail-loading">
        {error ? <p className="text-sm text-coral">Could not load job {jobId}: {error}</p> : [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
      </div>
    )
  }

  const turns = job.steps.filter((s) => s.status !== 'pending').length
  const openById = (id) => onOpenJob(id, null)

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="history-detail">
      <div className="space-y-4 border-b border-border bg-card/50 px-5 py-4 lg:px-7">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <StatusChip status={job.status} testId="history-detail-status" />
              <span className="text-xs text-muted-foreground">{job.steps_done}/{job.step_total} steps</span>
            </div>
            <h2 className="mt-2 truncate text-lg font-semibold tracking-tight" data-testid="history-detail-title">{job.title || 'Untitled roadmap'}</h2>
            <p className="flex flex-wrap gap-x-3 text-[12px] text-muted-foreground">
              <span className="font-mono">{job.id}</span>
              <span>created {formatDateTime(job.created_at)}</span>
              {job.finished_at && <span>finished {formatDateTime(job.finished_at)}</span>}
              {job.restarted_from && <JobLink label="restart of" id={job.restarted_from} onOpen={openById} />}
              {job.cloned_from && <JobLink label="clone of" id={job.cloned_from} onOpen={openById} />}
            </p>
            <p className="mt-1 truncate text-[12.5px]">
              <span className="font-mono font-medium">{job.config.model}</span>
              <span className="mx-1.5 text-muted-foreground">via</span>
              <span className="font-mono text-muted-foreground">{job.config.arena_url}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => onClone(job.id)} data-testid="clone-job-button"><Copy /> Clone job</Button>
            <Button size="sm" variant="outline" onClick={actions.exportTranscript} disabled={actions.exporting || !turns} data-testid="export-transcript-button">
              <FileDown /> {actions.exporting ? 'Exporting...' : 'Export transcript'}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={actions.download}
              disabled={job.steps_done < 1 || actions.downloading}
              data-testid="download-zip-button"
              className={cn(job.status === 'done' && 'border-teal/40 text-teal hover:text-teal')}
            >
              <Download /> {actions.downloading ? 'Packing...' : 'Download ZIP'}
            </Button>
          </div>
        </div>
        <JobControls job={job} busy={actions.busy} onStop={actions.stop} onResume={actions.resume} onRestart={actions.restart} />
        {(job.status === 'queued' || job.status === 'paused') && <QueuedAlert job={job} onOpenQueue={onOpenQueue} />}
        <JobAlerts job={job} onDownload={actions.download} />
      </div>

      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="min-w-0 overflow-x-auto border-b border-border px-5 py-2.5 lg:px-7">
          <TabsList className="h-9 bg-secondary">
            <TabsTrigger value="transcript" data-testid="detail-tab-transcript" className="px-3"><MessagesSquare /> Transcript <Count n={turns} /></TabsTrigger>
            <TabsTrigger value="files" data-testid="detail-tab-files" className="px-3"><FolderTree /> Files <Count n={job.artifacts.length} /></TabsTrigger>
            <TabsTrigger value="steps" data-testid="detail-tab-steps" className="px-3"><ListChecks /> Steps <Count n={job.step_total} /></TabsTrigger>
            <TabsTrigger value="log" data-testid="detail-tab-log" className="px-3"><ScrollText /> Log <Count n={job.log.length} /></TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="transcript" className="min-h-0 flex-1 overflow-y-auto">
          <FullTranscript jobId={job.id} version={`${job.status}:${job.steps_done}:${turns}`} />
        </TabsContent>
        <TabsContent value="files" className="min-h-[420px] flex-1">
          <ArtifactsPanel job={job} selectedPath={selectedPath} onSelect={setSelectedPath} />
        </TabsContent>
        <TabsContent value="steps" className="min-h-0 flex-1 overflow-y-auto p-5 lg:p-7">
          <RoadmapChecklist steps={job.steps} job={job} />
        </TabsContent>
        <TabsContent value="log" className="min-h-[420px] flex-1">
          <LogPanel job={job} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
