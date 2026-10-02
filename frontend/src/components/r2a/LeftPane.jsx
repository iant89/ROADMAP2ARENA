import { RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import StartForm from './StartForm'
import RoadmapChecklist from './RoadmapChecklist'
import JobAlerts from './JobAlerts'
import JobControls from './JobControls'

export default function LeftPane({
  form, setForm, steps, job, jobRunning, starting, onStart, onLoadSample, formExpanded, setFormExpanded, onClearJob, onDownload,
  controlsBusy, onStop, onResume, onRestart,
}) {
  return (
    <div className="space-y-6 p-5 lg:p-7">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{job ? 'Current job' : 'New job'}</h2>
          <p className="text-[13px] text-muted-foreground">
            {job ? <span className="font-mono">{job.id}</span> : 'Point at arena2api, paste a roadmap, and start.'}
            {job?.restarted_from && <span className="block text-xs">restart of <span className="font-mono">{job.restarted_from.slice(0, 8)}</span></span>}
          </p>
        </div>
        {job && !jobRunning && (
          <Button variant="outline" size="sm" onClick={onClearJob} data-testid="new-job-button">
            <RotateCcw /> New job
          </Button>
        )}
      </div>

      {job && (
        <JobControls job={job} anotherRunning={false} busy={controlsBusy} onStop={onStop} onResume={onResume} onRestart={onRestart} />
      )}

      <StartForm
        form={form}
        onChange={setForm}
        stepCount={job ? job.step_total : steps.length}
        onStart={onStart}
        onLoadSample={onLoadSample}
        starting={starting}
        jobRunning={jobRunning}
        collapsed={Boolean(job)}
        expanded={formExpanded}
        onToggleExpanded={setFormExpanded}
      />

      <JobAlerts job={job} onDownload={onDownload} />

      <Separator />

      <RoadmapChecklist steps={steps} job={job} />
    </div>
  )
}
