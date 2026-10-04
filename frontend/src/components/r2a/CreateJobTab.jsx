import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { parseRoadmap, startJob } from '@/lib/api'
import { SAMPLE_PROJECT_CONTEXT, SAMPLE_ROADMAP } from '@/constants/sampleRoadmap'
import StartForm from './StartForm'
import RoadmapChecklist from './RoadmapChecklist'

const PARSE_DEBOUNCE_MS = 250

// Create job tab: the start form plus a live preview of the parsed roadmap steps.
export default function CreateJobTab({ form, setForm, settings, queue, onCreated, onOpenSettings, onParseError }) {
  const [steps, setSteps] = useState([])
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      parseRoadmap(form.roadmap_md)
        .then((res) => { if (alive) { setSteps(res.steps); onParseError(null) } })
        .catch((err) => alive && onParseError(err.message))
    }, PARSE_DEBOUNCE_MS)
    return () => { alive = false; clearTimeout(t) }
  }, [form.roadmap_md, onParseError])

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const res = await startJob(form)
      onCreated(res, steps.length)
    } catch (err) {
      toast.error('Could not add job', { description: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  const handleLoadSample = () => {
    setForm((f) => ({ ...f, project_context: SAMPLE_PROJECT_CONTEXT, roadmap_md: SAMPLE_ROADMAP }))
    toast('Sample roadmap loaded', { description: 'Tasky - a FastAPI todo API in 7 steps' })
  }

  return (
    <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-6 p-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:p-7" data-testid="create-job-tab">
      <section className="space-y-5 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">New job</h2>
          <p className="text-[13px] text-muted-foreground">Pick a provider and model, paste a roadmap, and add it to the queue.</p>
        </div>
        <StartForm
          form={form}
          onChange={setForm}
          stepCount={steps.length}
          onSubmit={handleSubmit}
          onLoadSample={handleLoadSample}
          submitting={submitting}
          queueInfo={queue ? { running: Boolean(queue.running), waiting: queue.waiting } : null}
          settings={settings}
          onOpenSettings={onOpenSettings}
        />
      </section>
      <section className="rounded-xl border border-border bg-paper p-5 lg:p-6" data-testid="roadmap-preview">
        <RoadmapChecklist steps={steps} job={null} />
      </section>
    </div>
  )
}
