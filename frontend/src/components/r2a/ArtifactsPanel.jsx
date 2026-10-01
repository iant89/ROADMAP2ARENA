import { useMemo, useState } from 'react'
import { Check, ChevronRight, Copy, FileCode2, Folder, FolderOpen, PackageOpen } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

function buildTree(artifacts) {
  const root = { name: '', children: {}, files: [] }
  for (const a of artifacts) {
    const parts = a.path.replace(/\\/g, '/').replace(/^(\.\/|\/)+/, '').split('/').filter(Boolean)
    let node = root
    for (const dir of parts.slice(0, -1)) {
      node.children[dir] ??= { name: dir, children: {}, files: [] }
      node = node.children[dir]
    }
    node.files.push({ name: parts[parts.length - 1] || a.path, artifact: a })
  }
  return root
}

function TreeNode({ node, depth, selected, onSelect, collapsedDirs, toggleDir, prefix }) {
  const dirs = Object.values(node.children).sort((a, b) => a.name.localeCompare(b.name))
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  return (
    <ul>
      {dirs.map((d) => {
        const key = `${prefix}${d.name}/`
        const closed = collapsedDirs.has(key)
        return (
          <li key={key}>
            <button
              type="button"
              onClick={() => toggleDir(key)}
              className="flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-[13px] text-foreground/80 hover:bg-muted"
              style={{ paddingLeft: 8 + depth * 14 }}
            >
              <ChevronRight className={cn('size-3.5 text-muted-foreground transition-transform duration-150', !closed && 'rotate-90')} />
              {closed ? <Folder className="size-4 text-slate" /> : <FolderOpen className="size-4 text-slate" />}
              <span className="truncate">{d.name}</span>
            </button>
            {!closed && (
              <TreeNode node={d} depth={depth + 1} selected={selected} onSelect={onSelect} collapsedDirs={collapsedDirs} toggleDir={toggleDir} prefix={key} />
            )}
          </li>
        )
      })}
      {files.map(({ name, artifact }) => {
        const active = selected === artifact.path
        return (
          <li key={artifact.path}>
            <button
              type="button"
              data-testid={`file-tree-item-${artifact.path}`}
              onClick={() => onSelect(artifact.path)}
              className={cn(
                'flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-[13px] transition-colors duration-150',
                active ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-muted',
              )}
              style={{ paddingLeft: 8 + depth * 14 + 18 }}
            >
              <FileCode2 className={cn('size-4 shrink-0', active ? 'text-primary-foreground' : 'text-teal')} />
              <span className="truncate font-mono text-[12.5px]">{name}</span>
              {artifact.history.length > 1 && (
                <span className={cn('ml-auto shrink-0 font-mono text-[10px]', active ? 'text-primary-foreground/80' : 'text-amber')}>
                  v{artifact.history.length}
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function CodeView({ artifact, stepTitle }) {
  const [copied, setCopied] = useState(false)
  const lines = artifact.content.split('\n')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(artifact.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      toast.error('Clipboard is not available in this browser context')
    }
  }
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="code-pane">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-card px-4 py-2.5">
        <span className="min-w-0 truncate font-mono text-[13px] font-semibold" data-testid="code-pane-path">{artifact.path}</span>
        <Badge variant="outline" className="font-mono text-[11px]">{artifact.lang}</Badge>
        <span className="text-xs text-muted-foreground" data-testid="code-pane-step">
          from step {artifact.step_index}{stepTitle ? `: ${stepTitle}` : ''}
          {artifact.history.length > 1 && (
            <span className="text-amber"> (replaces step {artifact.history.slice(0, -1).join(', ')})</span>
          )}
        </span>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={copy}>
          {copied ? <Check className="text-teal" /> : <Copy />} {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1 bg-ink">
        <pre className="p-4 font-mono text-[12.5px] leading-[1.65] text-ink-foreground">
          {lines.map((line, i) => (
            <div key={i} className="flex">
              <span className="mr-4 inline-block w-8 shrink-0 text-right text-ink-foreground/40 select-none">{i + 1}</span>
              <span className="whitespace-pre">{line || ' '}</span>
            </div>
          ))}
        </pre>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
  )
}

export default function ArtifactsPanel({ job, selectedPath, onSelect }) {
  const [collapsedDirs, setCollapsedDirs] = useState(() => new Set())
  const artifacts = useMemo(() => job?.artifacts ?? [], [job])
  const tree = useMemo(() => buildTree(artifacts), [artifacts])
  const current = artifacts.find((a) => a.path === selectedPath) || null
  const stepTitle = current ? job.steps[current.step_index - 1]?.title : ''

  const toggleDir = (key) => setCollapsedDirs((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  if (!artifacts.length) {
    return (
      <div className="grid h-full place-items-center p-10" data-testid="artifacts-empty">
        <div className="max-w-sm text-center">
          <PackageOpen className="mx-auto size-9 text-slate" />
          <p className="mt-3 text-sm font-semibold">No artifacts yet</p>
          <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
            Files appear here as each step completes. Only fenced blocks with a file path
            (for example <code className="font-mono text-foreground">python:src/main.py</code>) are collected.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row" data-testid="artifacts-panel">
      <div className="flex max-h-56 shrink-0 flex-col border-b border-border bg-paper md:max-h-none md:w-64 md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-3 pt-3 pb-2">
          <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Files</span>
          <span className="font-mono text-xs text-muted-foreground">{artifacts.length}</span>
        </div>
        <ScrollArea className="min-h-0 flex-1 px-1.5 pb-3">
          <TreeNode node={tree} depth={0} selected={selectedPath} onSelect={onSelect} collapsedDirs={collapsedDirs} toggleDir={toggleDir} prefix="" />
        </ScrollArea>
      </div>
      <div className="min-h-[320px] min-w-0 flex-1">
        {current ? (
          <CodeView key={current.path + current.step_index} artifact={current} stepTitle={stepTitle} />
        ) : (
          <div className="grid h-full place-items-center p-8 text-[13px] text-muted-foreground">Select a file to view its contents.</div>
        )}
      </div>
    </div>
  )
}
