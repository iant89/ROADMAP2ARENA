import { Component, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Columns2, Rows2, WrapText } from 'lucide-react'
import { DiffAddedIcon, DiffIcon, DiffModifiedIcon, DiffRemovedIcon, DiffRenamedIcon, FileBinaryIcon } from '@primer/octicons-react'
import { DiffModeEnum, DiffView } from '@git-diff-view/react'
import '@git-diff-view/react/styles/diff-view-pure.css'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// Shared diff viewer (Git tab commits, step compare; later the repo browser: branch compare,
// uncommitted changes, PR diffs). Input: the backend's structured diff (git_diff.py):
// files: [{path, old_path, status, additions, deletions, binary, patch, patch_too_large,
//          old_content, new_content, context_expandable}].
// Split/unified toggle (remembered), syntax highlighting, word-level changes, unchanged context
// collapsed into expandable separators (when file contents are available), file list with +/-.

const MODE_KEY = 'r2a.diffMode'
const AUTO_COLLAPSE_LINES = 1500 // big files start collapsed
const STATUS = {
  added: { icon: DiffAddedIcon, cls: 'text-teal', label: 'added' },
  deleted: { icon: DiffRemovedIcon, cls: 'text-coral', label: 'deleted' },
  modified: { icon: DiffModifiedIcon, cls: 'text-amber-700', label: 'modified' },
  renamed: { icon: DiffRenamedIcon, cls: 'text-slate', label: 'renamed' },
  copied: { icon: DiffRenamedIcon, cls: 'text-slate', label: 'copied' },
}

function initialMode() {
  const saved = typeof localStorage !== 'undefined' ? localStorage.getItem(MODE_KEY) : null
  if (saved === 'split' || saved === 'unified') return saved
  return typeof window !== 'undefined' && window.innerWidth < 768 ? 'unified' : 'split'
}

function Counts({ add, del }) {
  return (
    <span className="shrink-0 font-mono text-[11px]">
      <span className="text-teal">+{add ?? '?'}</span> <span className="text-coral">-{del ?? '?'}</span>
    </span>
  )
}

// A malformed patch must not take the whole panel down: fall back to the raw text.
class FileBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false } }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) {
      return <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-[1.5]" data-testid="diff-raw">{this.props.patch}</pre>
    }
    return this.props.children
  }
}

function FileDiff({ file, mode, wrap, open, onToggle, index }) {
  const meta = STATUS[file.status] ?? { icon: DiffIcon, cls: 'text-slate', label: file.status }
  const Icon = file.binary ? FileBinaryIcon : meta.icon
  const data = useMemo(() => (file.patch ? {
    oldFile: { fileName: file.old_path || file.path, content: file.context_expandable ? file.old_content ?? '' : null },
    newFile: { fileName: file.path, content: file.context_expandable ? file.new_content ?? '' : null },
    hunks: [file.patch],
  } : null), [file])
  const hasHunks = Boolean(file.patch && file.patch.includes('\n@@'))
  return (
    <section id={`diff-file-${index}`} className="overflow-hidden rounded-lg border border-border bg-card" data-testid="diff-file" data-path={file.path}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="sticky top-0 z-10 flex w-full items-center gap-2 border-b border-border bg-paper px-3 py-2 text-left hover:bg-muted"
        data-testid="diff-file-toggle"
      >
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <Icon size={16} className={cn('shrink-0', meta.cls)} aria-label={meta.label} />
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]" title={file.path}>
          {file.old_path && file.old_path !== file.path ? <>{file.old_path} <span className="text-muted-foreground">-&gt;</span> {file.path}</> : file.path}
        </span>
        <Counts add={file.additions} del={file.deletions} />
      </button>
      {open && (
        <div className="text-[12px]" data-testid="diff-file-body">
          {file.binary && <p className="px-3 py-2 text-[12.5px] text-muted-foreground">Binary file - no text diff.</p>}
          {file.patch_too_large && <p className="px-3 py-2 text-[12.5px] text-muted-foreground" data-testid="diff-too-large">This diff is too large to show - download the repository to inspect it.</p>}
          {data && !hasHunks && <p className="px-3 py-2 text-[12.5px] text-muted-foreground">{file.status === 'renamed' ? 'Renamed without content changes.' : 'No content changes (mode or empty file).'}</p>}
          {data && hasHunks && (
            <FileBoundary patch={file.patch}>
              <div className="r2a-diff overflow-x-auto">
                <DiffView
                  data={data}
                  diffViewMode={mode === 'split' ? DiffModeEnum.Split : DiffModeEnum.Unified}
                  diffViewTheme="light"
                  diffViewHighlight
                  diffViewWrap={wrap}
                  diffViewFontSize={12}
                />
              </div>
            </FileBoundary>
          )}
        </div>
      )}
    </section>
  )
}

export default function DiffViewer({ diff, testId = 'diff-viewer', emptyText = 'No changes.' }) {
  const files = useMemo(() => diff?.files ?? [], [diff])
  const [mode, setMode] = useState(initialMode)
  const [wrap, setWrap] = useState(false)
  const [closed, setClosed] = useState(() => new Set(
    files.map((f, i) => ((f.additions ?? 0) + (f.deletions ?? 0) > AUTO_COLLAPSE_LINES || i >= 25 ? i : -1)).filter((i) => i >= 0),
  ))
  const root = useRef(null)
  useEffect(() => { localStorage.setItem(MODE_KEY, mode) }, [mode])

  const toggle = (i) => setClosed((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n })
  const jump = (i) => {
    setClosed((s) => { const n = new Set(s); n.delete(i); return n })
    requestAnimationFrame(() => root.current?.querySelector(`#diff-file-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
  const stats = diff?.stats ?? { files: files.length, additions: 0, deletions: 0 }

  if (!files.length) return <p className="p-4 text-[13px] text-muted-foreground" data-testid={`${testId}-empty`}>{emptyText}</p>
  return (
    <div ref={root} className="space-y-3" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[12.5px] font-medium" data-testid="diff-stats">
          {stats.files} file{stats.files === 1 ? '' : 's'} changed <Counts add={stats.additions} del={stats.deletions} />
        </p>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Diff layout">
            <Button size="xs" variant={mode === 'split' ? 'secondary' : 'ghost'} aria-pressed={mode === 'split'} onClick={() => setMode('split')} data-testid="diff-mode-split" title="Side by side">
              <Columns2 /> <span className="hidden sm:inline">Split</span>
            </Button>
            <Button size="xs" variant={mode === 'unified' ? 'secondary' : 'ghost'} aria-pressed={mode === 'unified'} onClick={() => setMode('unified')} data-testid="diff-mode-unified" title="One column">
              <Rows2 /> <span className="hidden sm:inline">Unified</span>
            </Button>
          </div>
          <Button size="xs" variant={wrap ? 'secondary' : 'ghost'} aria-pressed={wrap} onClick={() => setWrap((w) => !w)} title="Wrap long lines" aria-label="Wrap long lines" data-testid="diff-wrap"><WrapText /></Button>
          <Button size="xs" variant="ghost" onClick={() => setClosed(new Set())} title="Expand all files" aria-label="Expand all files" data-testid="diff-expand-all"><ChevronsUpDown /></Button>
          <Button size="xs" variant="ghost" onClick={() => setClosed(new Set(files.map((_, i) => i)))} title="Collapse all files" aria-label="Collapse all files" data-testid="diff-collapse-all"><ChevronsDownUp /></Button>
        </div>
      </div>
      <ul className="divide-y divide-border rounded-lg border border-border bg-card" data-testid="diff-file-list">
        {files.map((f, i) => {
          const meta = STATUS[f.status] ?? { icon: DiffIcon, cls: 'text-slate', label: f.status }
          const Icon = f.binary ? FileBinaryIcon : meta.icon
          return (
            <li key={`${f.path}:${i}`}>
              <button type="button" onClick={() => jump(i)} className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] hover:bg-muted" data-testid="diff-file-link">
                <Icon size={16} className={cn('shrink-0', meta.cls)} aria-label={meta.label} />
                <span className="min-w-0 flex-1 truncate font-mono">{f.path}</span>
                <span className={cn('hidden text-[11px] font-medium sm:inline', meta.cls)}>{meta.label}</span>
                <Counts add={f.additions} del={f.deletions} />
              </button>
            </li>
          )
        })}
      </ul>
      {files.map((f, i) => (
        <FileDiff key={`${f.path}:${i}`} file={f} index={i} mode={mode} wrap={wrap} open={!closed.has(i)} onToggle={() => toggle(i)} />
      ))}
      {diff?.truncated && <p className="text-[12px] text-amber-800" data-testid="diff-truncated">Some files are not shown (the diff is very large) - download the repository to see everything.</p>}
    </div>
  )
}
