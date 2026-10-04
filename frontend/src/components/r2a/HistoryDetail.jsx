import { useEffect, useState } from 'react'
import { Copy, Download, FileDown, FolderTree, ListChecks, MessagesSquare, ScrollText, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { GitCommitIcon, MarkGithubIcon } from '@primer/octicons-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useJob } from '@/hooks/useJob'
import { useJobActions } from '@/hooks/useJobActions'
import { deleteJob } from '@/lib/api'
import ArtifactsPanel from './ArtifactsPanel'
import ConfirmButton from './ConfirmButton'
import FullTranscript from './FullTranscript'
import GitPanel from './GitPanel'
import PushToGitHubSheet from './PushToGitHubSheet'
import JobAlerts from './JobAlerts'
import JobControls from './JobControls'
import LogPanel from './LogPanel'
import RoadmapChecklist from './RoadmapChecklist'
import { QueuedAlert } from './JobView'
import { StatusChip, formatDateTime, providerName } from './status'

function Count({ n }) {
  return <span className="hidden rounded-full bg-secondary px-1.5 font-mono sm:inline-flex text-[10.5px] text-secondary-foreground">{n}</span>
}

function JobLink({ label, id, onOpen }) {
  return (
    <span>{label} <button type="button" className="font-mono underline underline-offset-2 hover:text-foreground" onClick={() => onOpen(id)}>{id.slice(0, 8)}</button></span>
  )
}

// Job history detail: header with actions (Clone, transcript export, ZIP, Resume/Restart/Stop)
// and Transcript | Files | Steps | Git | Log tabs.
export default function HistoryDetail({ jobId, onOpenJob, onOpenQueue, onQueueChanged, onClone, onDeleted }) {
  const { job, error, refresh, restartPolling } = useJob(jobId)
  const actions = useJobActions(job, { refresh, restartPolling, onOpenJob, onQueueChanged })
  const [tab, setTab] = useState('transcript')
  const [selectedPath, setSelectedPath] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [pushOpen, setPushOpen] = useState(false)

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
  const running = job.status === 'running'
  const remove = async () => {
    setDeleting(true)
    try {
      await deleteJob(job.id)
      toast.success('Job deleted', { description: `${job.title || 'Untitled roadmap'} - steps and files removed` })
      onDeleted?.([job.id])
    } catch (err) {
      if (err.status === 404) onDeleted?.([job.id])
      else toast.error('Could not delete the job', { description: err.message })
      setDeleting(false)
    }
  }
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
              <span className="font-medium" data-testid="job-provider-name">{providerName(job.config.provider)}</span>
              <span className="ml-1.5 font-mono text-muted-foreground" title={job.config.arena_url}>{job.config.provider?.base_url || job.config.arena_url}</span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={() => onClone(job.id)} data-testid="clone-job-button"><Copy /> Clone job</Button>
            <Button size="sm" variant="outline" onClick={actions.exportTranscript} disabled={actions.exporting || !turns} data-testid="export-transcript-button">
              <FileDown /> {actions.exporting ? 'Exporting...' : 'Export transcript'}
            </Button>
            {job.status !== 'done' && (
              <Button
                size="sm"
                variant="outline"
                onClick={actions.download}
                disabled={job.steps_done < 1 || actions.downloading}
                data-testid="download-zip-button"
              >
                <Download /> {actions.downloading ? 'Packing...' : 'Download ZIP'}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setPushOpen(true)}
              disabled={running || job.steps_done < 1}
              title={running ? 'Wait until the job finishes' : job.steps_done < 1 ? 'No completed steps yet' : 'Push the git history to GitHub'}
              data-testid="push-github-button"
            >
              <MarkGithubIcon size={16} /> Push to GitHub
            </Button>
            <ConfirmButton
              onConfirm={remove}
              busy={deleting}
              disabled={running}
              testId="delete-job-button"
              confirmLabel={<><Trash2 /> Delete permanently?</>}
              title={running ? 'Stop the job first, then delete it' : 'Delete this job with its steps and files'}
            >
              <Trash2 /> {deleting ? 'Deleting...' : 'Delete'}
            </ConfirmButton>
          </div>
        </div>
        <JobControls job={job} busy={actions.busy} onStop={actions.stop} onResume={actions.resume} onRestart={actions.restart} />
        {(job.status === 'queued' || job.status === 'paused') && <QueuedAlert job={job} onOpenQueue={onOpenQueue} />}
        <JobAlerts job={job} onDownload={actions.download} downloading={actions.downloading} />
        {running && <p className="text-[12px] text-muted-foreground" data-testid="delete-running-hint">A running job can't be deleted - stop it first.</p>}
      </div>

      <PushToGitHubSheet job={job} open={pushOpen} onOpenChange={setPushOpen} onPushed={() => refresh?.()} />
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="min-w-0 overflow-x-auto border-b border-border px-5 py-2.5 lg:px-7">
          <TabsList className="grid h-9 w-full grid-cols-5 bg-secondary sm:inline-flex sm:w-fit">
            <TabsTrigger value="transcript" data-testid="detail-tab-transcript" className="px-2 text-xs sm:px-3 sm:text-sm"><MessagesSquare /> Transcript <Count n={turns} /></TabsTrigger>
            <TabsTrigger value="files" data-testid="detail-tab-files" className="px-2 text-xs sm:px-3 sm:text-sm"><FolderTree /> Files <Count n={job.artifacts.length} /></TabsTrigger>
            <TabsTrigger value="steps" data-testid="detail-tab-steps" className="px-2 text-xs sm:px-3 sm:text-sm"><ListChecks /> Steps <Count n={job.step_total} /></TabsTrigger>
            <TabsTrigger value="git" data-testid="detail-tab-git" className="px-2 text-xs sm:px-3 sm:text-sm"><GitCommitIcon size={16} /> Git <Count n={job.steps.filter((s) => s.commit_sha).length} /></TabsTrigger>
            <TabsTrigger value="log" data-testid="detail-tab-log" className="px-2 text-xs sm:px-3 sm:text-sm"><ScrollText /> Log <Count n={job.log.length} /></TabsTrigger>
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
        <TabsContent value="git" className="min-h-[420px] flex-1">
          <GitPanel key={job.id} job={job} version={`${job.status}:${job.steps.filter((s) => s.commit_sha).length}:${job.log.length}`} />
        </TabsContent>
        <TabsContent value="log" className="min-h-[420px] flex-1">
          <LogPanel job={job} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
