import { useState } from 'react'
import { ArrowLeft, History } from 'lucide-react'
import { Button } from '@/components/ui/button'
import HistoryPanel from './HistoryPanel'
import HistoryDetail from './HistoryDetail'
import CloneJobSheet from './CloneJobSheet'

// Job history tab: collapsible job list on the left, the opened job (?job=<id>) on the right.
export default function HistoryTab({ jobId, onOpen, onOpenJob, onOpenQueue, onQueueChanged, refreshKey, isDesktop, queue, settings, onCloned }) {
  const [cloneId, setCloneId] = useState(null)
  return (
    <div className={isDesktop ? 'flex h-full min-h-0' : 'flex flex-col'} data-testid="history-tab">
      {/* Mobile uses a list/detail pattern: with a job open, the list gives way to a back bar. */}
      {isDesktop || !jobId ? (
        <HistoryPanel selectedId={jobId} onSelect={onOpen} refreshKey={refreshKey} isDesktop={isDesktop} />
      ) : (
        <div className="border-b border-border bg-paper px-3 py-2">
          <Button variant="ghost" size="sm" onClick={() => onOpen(null)} data-testid="history-back-to-list">
            <ArrowLeft /> All jobs
          </Button>
        </div>
      )}
      <div className={isDesktop ? 'min-w-0 flex-1' : ''}>
        {jobId ? (
          <HistoryDetail key={jobId} jobId={jobId} onOpenJob={onOpenJob} onOpenQueue={onOpenQueue} onQueueChanged={onQueueChanged} onClone={setCloneId} />
        ) : isDesktop && (
          <div className="grid h-full min-h-[300px] place-items-center p-10 text-center" data-testid="history-no-selection">
            <div className="max-w-sm">
              <History className="mx-auto size-8 text-muted-foreground" />
              <p className="mt-3 text-sm font-semibold">Select a job</p>
              <p className="mt-1 text-[13px] text-muted-foreground">Pick a job from the list to read its full transcript, view or download its files, export the conversation as HTML, or clone it into a new job.</p>
            </div>
          </div>
        )}
      </div>
      <CloneJobSheet
        sourceId={cloneId}
        open={Boolean(cloneId)}
        onOpenChange={(o) => !o && setCloneId(null)}
        queue={queue}
        settings={settings}
        onCreated={onCloned}
      />
    </div>
  )
}
