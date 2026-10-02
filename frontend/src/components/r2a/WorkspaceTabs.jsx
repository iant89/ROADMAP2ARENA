import { FolderTree, MessagesSquare, ScrollText } from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import ArtifactsPanel from './ArtifactsPanel'
import TranscriptPanel from './TranscriptPanel'
import LogPanel from './LogPanel'

function Count({ n }) {
  return <span className="hidden rounded-full bg-secondary px-1.5 font-mono sm:inline-flex text-[10.5px] text-secondary-foreground">{n}</span>
}

export default function WorkspaceTabs({ job, tab, setTab, selectedPath, setSelectedPath }) {
  const transcriptCount = (job?.steps ?? []).filter((s) => s.status !== 'pending').length
  return (
    <Tabs value={tab} onValueChange={setTab} className="flex h-full min-h-0 flex-col gap-0">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
        <TabsList className="grid h-9 w-full grid-cols-3 bg-secondary sm:inline-flex sm:w-fit">
          <TabsTrigger value="artifacts" data-testid="tab-artifacts" className="px-2 text-xs sm:px-3 sm:text-sm"><FolderTree /> Artifacts <Count n={job?.artifacts.length ?? 0} /></TabsTrigger>
          <TabsTrigger value="transcript" data-testid="tab-transcript" className="px-2 text-xs sm:px-3 sm:text-sm"><MessagesSquare /> Transcript <Count n={transcriptCount} /></TabsTrigger>
          <TabsTrigger value="log" data-testid="tab-log" className="px-2 text-xs sm:px-3 sm:text-sm"><ScrollText /> Log <Count n={job?.log.length ?? 0} /></TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="artifacts" className="min-h-0 flex-1">
        <ArtifactsPanel job={job} selectedPath={selectedPath} onSelect={setSelectedPath} />
      </TabsContent>
      <TabsContent value="transcript" className="min-h-0 flex-1">
        <TranscriptPanel job={job} />
      </TabsContent>
      <TabsContent value="log" className="min-h-0 flex-1">
        <LogPanel job={job} />
      </TabsContent>
    </Tabs>
  )
}
