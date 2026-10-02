import { useCallback, useEffect, useState } from 'react'
import { Archive, FileCode2, RefreshCw } from 'lucide-react'
import { GitBranchIcon, GitCommitIcon, PackageIcon, RepoIcon } from '@primer/octicons-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { downloadJobRepo, getJobCommit, getJobGit, initJobGit } from '@/lib/api'
import { formatDateTime } from './status'

const STATUS_STYLE = {
  added: 'text-teal', modified: 'text-amber-700', deleted: 'text-coral', renamed: 'text-slate',
}

function PatchView({ patch, truncated }) {
  if (!patch) return <p className="p-4 text-[13px] text-muted-foreground">No changes in this commit.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-[#14171C]" data-testid="git-patch">
      <pre className="min-w-fit p-3 font-mono text-[12px] leading-[1.5]">
        {patch.split('\n').map((line, i) => (
          <div
            key={i}
            className={cn(
              'whitespace-pre px-1',
              line.startsWith('+') && !line.startsWith('+++') && 'bg-[#123326] text-[#7EE2B8]',
              line.startsWith('-') && !line.startsWith('---') && 'bg-[#3A1A17] text-[#FFA28F]',
              line.startsWith('@@') && 'text-[#8AB4F8]',
              (line.startsWith('diff --git') || line.startsWith('+++') || line.startsWith('---') || line.startsWith('index ') || line.startsWith('new file')) && 'text-[#9FB3C8]',
              !/^[-+@di]|^new/.test(line) && 'text-[#D6DCE4]',
            )}
          >{line || ' '}</div>
        ))}
      </pre>
      {truncated && <p className="border-t border-white/10 px-3 py-2 text-[12px] text-[#F2C266]" data-testid="git-patch-truncated">Diff truncated - download the repository to see everything.</p>}
    </div>
  )
}

function CommitDetail({ jobId, sha }) {
  const [state, setState] = useState({ sha: null, commit: null, error: null })
  useEffect(() => {
    let alive = true
    getJobCommit(jobId, sha)
      .then((commit) => alive && setState({ sha, commit, error: null }))
      .catch((err) => alive && setState({ sha, commit: null, error: err.message }))
    return () => { alive = false }
  }, [jobId, sha])
  if (state.error && state.sha === sha) return <p className="p-4 text-sm text-coral" data-testid="git-commit-error">{state.error}</p>
  const commit = state.sha === sha ? state.commit : null
  if (!commit) return <div className="space-y-2 p-4"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-40" /></div>
  return (
    <div className="space-y-3 p-4" data-testid="git-commit-detail">
      <div>
        <p className="text-[15px] font-semibold" data-testid="git-commit-subject">{commit.subject}</p>
        <p className="mt-0.5 text-[12px] text-muted-foreground">
          <span className="font-mono">{commit.short_sha}</span> by {commit.author_name} - {formatDateTime(commit.date)}
          {commit.parents.length === 0 && ' - first commit'}
        </p>
      </div>
      <div className="rounded-lg border border-border bg-card">
        <p className="border-b border-border px-3 py-1.5 text-[12px] font-medium text-muted-foreground">
          {commit.files.length} file{commit.files.length === 1 ? '' : 's'} changed
        </p>
        <ul className="divide-y divide-border" data-testid="git-commit-files">
          {commit.files.map((f) => (
            <li key={f.path} className="flex items-center gap-2 px-3 py-1.5 text-[12.5px]">
              <FileCode2 className="size-3.5 shrink-0 text-slate" />
              <span className="min-w-0 flex-1 truncate font-mono">{f.path}</span>
              <span className={cn('text-[11px] font-medium', STATUS_STYLE[f.status])}>{f.status}</span>
              <span className="w-16 text-right font-mono text-[11px]">
                <span className="text-teal">+{f.additions ?? '?'}</span> <span className="text-coral">-{f.deletions ?? '?'}</span>
              </span>
            </li>
          ))}
          {!commit.files.length && <li className="px-3 py-2 text-[12.5px] text-muted-foreground">No files (the step produced no named files).</li>}
        </ul>
      </div>
      <PatchView patch={commit.patch} truncated={commit.patch_truncated} />
    </div>
  )
}

// Git tab of the job detail: the job's local repo, one commit per completed step.
export default function GitPanel({ job, version }) {
  const [git, setGit] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(null)

  const load = useCallback(async () => {
    try {
      const g = await getJobGit(job.id)
      setGit(g)
      setError(null)
      setSelected((cur) => (g.commits.some((c) => c.sha === cur) ? cur : g.commits[0]?.sha ?? null))
    } catch (err) {
      setError(err.message)
    }
  }, [job.id])

  useEffect(() => { load() }, [load, version])

  const download = async (format) => {
    setBusy(format)
    try {
      const res = await downloadJobRepo(job.id, format)
      toast.success(format === 'bundle' ? 'Git bundle downloaded' : 'Repository downloaded', {
        description: `${res.filename} (${Math.max(1, Math.round(res.size / 1024))} KB)${format === 'bundle' ? ' - clone it with git clone <file>' : ' - includes the .git folder'}`,
      })
    } catch (err) {
      toast.error('Download failed', { description: err.message })
    } finally {
      setBusy(null)
    }
  }
  const init = async () => {
    setBusy('init')
    try {
      const g = await initJobGit(job.id)
      toast.success('Repository created', { description: `${g.committed_steps.length} step commit${g.committed_steps.length === 1 ? '' : 's'}` })
      await load()
    } catch (err) {
      toast.error('Could not create the repository', { description: err.message })
    } finally {
      setBusy(null)
    }
  }

  if (error && !git) return <p className="p-6 text-sm text-coral" data-testid="git-error">Could not load the git history: {error}</p>
  if (!git) return <div className="space-y-3 p-6">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 rounded-lg" />)}</div>

  if (!git.exists) {
    const canInit = git.uncommitted_steps > 0 && job.status !== 'running'
    return (
      <div className="grid h-full place-items-center p-10 text-center" data-testid="git-empty">
        <div className="max-w-sm">
          <RepoIcon size={36} className="mx-auto text-slate" />
          <p className="mt-3 text-sm font-semibold">No git repository yet</p>
          <p className="mt-1.5 text-[13px] text-muted-foreground">
            {canInit
              ? `This job ran before git history existed. Create a repository with one commit per done step (${git.uncommitted_steps}).`
              : 'The repository is created when the job starts; every completed step becomes a commit.'}
          </p>
          {canInit && <Button size="sm" className="mt-4" onClick={init} disabled={busy === 'init'} data-testid="git-init-button"><RepoIcon size={16} /> {busy === 'init' ? 'Creating...' : 'Create repository'}</Button>}
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="git-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <GitBranchIcon size={16} className="text-slate" />
        <span className="font-mono text-[13px] font-medium" data-testid="git-branch">{git.default_branch}</span>
        <span className="text-[12px] text-muted-foreground" data-testid="git-commit-count">{git.commit_count} commit{git.commit_count === 1 ? '' : 's'}</span>
        {git.head && <span className="font-mono text-[12px] text-muted-foreground">HEAD {git.head.slice(0, 7)}</span>}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" onClick={load} title="Refresh" aria-label="Refresh git history" data-testid="git-refresh"><RefreshCw /></Button>
          <Button size="sm" variant="outline" onClick={() => download('zip')} disabled={!git.commit_count || !!busy} data-testid="git-download-zip" title="ZIP of the working tree including the .git folder">
            <Archive /> {busy === 'zip' ? 'Packing...' : 'Download repo'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => download('bundle')} disabled={!git.commit_count || !!busy} data-testid="git-download-bundle" title="Single-file git bundle (git clone file.bundle)">
            <PackageIcon size={16} /> {busy === 'bundle' ? 'Bundling...' : 'Bundle'}
          </Button>
        </div>
      </div>
      {git.uncommitted_steps > 0 && job.status !== 'running' && (
        <p className="border-b border-border bg-amber-50 px-4 py-1.5 text-[12px] text-amber-800" data-testid="git-uncommitted">
          {git.uncommitted_steps} done step{git.uncommitted_steps === 1 ? ' has' : 's have'} no commit (see the Log).{' '}
          <button type="button" className="underline" onClick={init}>Commit now</button>
        </p>
      )}
      {!git.commits.length ? (
        <p className="p-6 text-[13px] text-muted-foreground" data-testid="git-no-commits">No commits yet - each completed step is committed here.</p>
      ) : (
        <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,auto)_minmax(0,1fr)] lg:grid-cols-[minmax(240px,320px)_minmax(0,1fr)] lg:grid-rows-1">
          <ul className="max-h-60 overflow-y-auto border-b border-border lg:max-h-none lg:border-r lg:border-b-0" data-testid="git-commit-list">
            {git.commits.map((c) => (
              <li key={c.sha}>
                <button
                  type="button"
                  onClick={() => setSelected(c.sha)}
                  aria-pressed={selected === c.sha}
                  data-testid={`git-commit-${c.short_sha}`}
                  className={cn('flex w-full items-start gap-2.5 border-l-2 px-3 py-2.5 text-left hover:bg-muted', selected === c.sha ? 'border-l-teal bg-muted' : 'border-l-transparent')}
                >
                  <GitCommitIcon size={16} className="mt-0.5 shrink-0 text-slate" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{c.message}</span>
                    <span className="block text-[11.5px] text-muted-foreground">
                      <span className="font-mono">{c.short_sha}</span> - {c.files_changed} file{c.files_changed === 1 ? '' : 's'}{' '}
                      <span className="text-teal">+{c.insertions}</span> <span className="text-coral">-{c.deletions}</span>
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-h-0 overflow-y-auto">{selected && <CommitDetail jobId={job.id} sha={selected} />}</div>
        </div>
      )}
    </div>
  )
}
