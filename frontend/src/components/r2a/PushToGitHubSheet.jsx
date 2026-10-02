import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { CircleAlert, ExternalLink, Lock, Search, Settings, Unlock } from 'lucide-react'
import { GitBranchIcon, GitCommitIcon, GitPullRequestIcon, MarkGithubIcon, RepoIcon, RepoPushIcon } from '@primer/octicons-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { getJobGitHub, listGitHubRepos, pushJobToGitHub } from '@/lib/api'
import { formatDateTime } from './status'

const fieldCls = 'h-9 bg-card font-mono text-base sm:text-sm'

function ResultLinks({ res, testId }) {
  return (
    <div className="space-y-1.5 rounded-lg border border-teal/30 bg-teal-soft px-3.5 py-3 text-[13px]" data-testid={testId}>
      <p className="font-semibold text-teal">Pushed {res.commit_count} commit{res.commit_count === 1 ? '' : 's'}{res.pushed_at ? ` - ${formatDateTime(res.pushed_at)}` : ''}</p>
      <a className="flex items-center gap-1.5 underline underline-offset-2" href={res.repo.html_url} target="_blank" rel="noreferrer" data-testid={`${testId}-repo`}>
        <RepoIcon size={16} /> {res.repo.full_name}{res.repo.created ? ' (new)' : ''} <ExternalLink className="size-3" />
      </a>
      {res.branch_url && (
        <a className="flex items-center gap-1.5 underline underline-offset-2" href={res.branch_url} target="_blank" rel="noreferrer" data-testid={`${testId}-branch`}>
          <GitBranchIcon size={16} /> {res.branch} <ExternalLink className="size-3" />
        </a>
      )}
      {res.commit_url && (
        <a className="flex items-center gap-1.5 underline underline-offset-2" href={res.commit_url} target="_blank" rel="noreferrer">
          <GitCommitIcon size={16} /> {res.head.slice(0, 7)} <ExternalLink className="size-3" />
        </a>
      )}
      {res.pr && (
        <a className="flex items-center gap-1.5 font-medium underline underline-offset-2" href={res.pr.html_url} target="_blank" rel="noreferrer" data-testid={`${testId}-pr`}>
          <GitPullRequestIcon size={16} /> Pull request #{res.pr.number}{res.pr.existing ? ' (already open)' : ''} <ExternalLink className="size-3" />
        </a>
      )}
      {res.pr_error && <p className="flex items-start gap-1.5 text-xs font-medium text-coral" data-testid={`${testId}-pr-error`}><CircleAlert className="mt-0.5 size-3.5 shrink-0" /> {res.pr_error}</p>}
    </div>
  )
}

// "Push to GitHub" for one job: new or existing repo, branch, optional pull request.
export default function PushToGitHubSheet({ job, open, onOpenChange, onPushed }) {
  const [info, setInfo] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [mode, setMode] = useState('new')
  const [form, setForm] = useState(null)
  const [repos, setRepos] = useState(null)
  const [reposError, setReposError] = useState(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => {
    if (!open) return undefined
    let alive = true
    setResult(null); setError(null); setLoadError(null)
    getJobGitHub(job.id).then((res) => {
      if (!alive) return
      setInfo(res)
      const last = res.last_push
      setMode(last ? 'existing' : 'new')
      setForm({
        repo_name: res.defaults.repo_name, private: true, repo_full_name: last?.repo.full_name || '',
        branch: last?.branch || res.defaults.branch, open_pr: false, pr_base: '',
        pr_title: res.defaults.pr_title, pr_body: res.defaults.pr_body,
      })
    }).catch((err) => alive && setLoadError(err.message))
    return () => { alive = false }
  }, [open, job.id])

  useEffect(() => {
    if (!open || mode !== 'existing' || repos || !info?.connected) return
    listGitHubRepos().then((r) => setRepos(r.items)).catch((err) => setReposError(err.message))
  }, [open, mode, repos, info])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (repos || []).filter((r) => !q || r.full_name.toLowerCase().includes(q)).slice(0, 100)
  }, [repos, query])
  const selectedRepo = (repos || []).find((r) => r.full_name === form?.repo_full_name)
  const set = (patch) => setForm((f) => ({ ...f, ...patch }))

  const submit = async () => {
    setBusy(true); setError(null); setResult(null)
    const body = mode === 'new'
      ? { mode, repo_name: form.repo_name.trim(), private: form.private, branch: form.branch.trim() }
      : { mode, repo_full_name: form.repo_full_name, branch: form.branch.trim(), open_pr: form.open_pr,
        ...(form.open_pr ? { pr_base: form.pr_base.trim() || null, pr_title: form.pr_title, pr_body: form.pr_body } : {}) }
    try {
      const res = await pushJobToGitHub(job.id, body)
      setResult(res)
      toast.success(`Pushed to ${res.repo.full_name}`, { description: res.pr ? `Pull request #${res.pr.number}` : `Branch ${res.branch}` })
      onPushed?.(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = form && form.branch.trim() && (mode === 'new' ? form.repo_name.trim() : form.repo_full_name) && !busy

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 bg-background sm:max-w-xl" data-testid="push-github-sheet">
        <SheetHeader className="border-b border-border bg-card px-5 py-4">
          <SheetTitle className="flex items-center gap-2"><MarkGithubIcon size={16} /> Push to GitHub</SheetTitle>
          <SheetDescription>
            Pushes this job's git history ({job.steps.filter((s) => s.commit_sha).length} step commits) to a branch. Never force-pushes.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-5">
            {loadError && <p className="text-sm text-coral">{loadError}</p>}
            {!info && !loadError && [0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-lg" />)}
            {info && !info.connected && (
              <div className="space-y-3 rounded-lg border border-border bg-paper p-4 text-[13px]" data-testid="gh-not-connected">
                <p className="font-semibold">GitHub is not connected</p>
                <p className="text-muted-foreground">Add a personal access token in Settings first.</p>
                <Button size="sm" asChild><a href="?tab=settings" data-testid="gh-open-settings"><Settings /> Open Settings</a></Button>
              </div>
            )}
            {info?.connected && form && (
              <>
                {info.last_push && !result && <ResultLinks res={info.last_push} testId="gh-last-push" />}
                <div className="grid grid-cols-2 gap-2" role="group" aria-label="Target repository">
                  {[['new', 'New repository', RepoIcon], ['existing', 'Existing repository', RepoPushIcon]].map(([m, label, Icon]) => (
                    <Button key={m} variant={mode === m ? 'default' : 'outline'} aria-pressed={mode === m} onClick={() => setMode(m)} data-testid={`gh-mode-${m}`}>
                      <Icon size={16} /> {label}
                    </Button>
                  ))}
                </div>
                {mode === 'new' ? (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="gh-repo-name" className="text-[13px] font-semibold">Repository name</Label>
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[13px] text-muted-foreground">{info.username}/</span>
                        <Input id="gh-repo-name" className={fieldCls} value={form.repo_name} onChange={(e) => set({ repo_name: e.target.value })} data-testid="gh-repo-name" />
                      </div>
                    </div>
                    <label className="flex items-center gap-2.5 text-[13px]">
                      <input type="checkbox" className="size-4 accent-primary" checked={form.private} onChange={(e) => set({ private: e.target.checked })} data-testid="gh-private" />
                      {form.private ? <Lock className="size-4" /> : <Unlock className="size-4" />} Private repository
                    </label>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="gh-repo-search" className="text-[13px] font-semibold">Repository</Label>
                    <div className="relative">
                      <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
                      <Input id="gh-repo-search" className={cn(fieldCls, 'pl-8')} placeholder="Search your repositories" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="gh-repo-search" />
                    </div>
                    {reposError && <p className="text-xs text-coral" data-testid="gh-repos-error">{reposError}</p>}
                    {!repos && !reposError && <Skeleton className="h-32 rounded-lg" />}
                    {repos && (
                      <ul className="max-h-56 overflow-y-auto rounded-lg border border-border bg-card" role="listbox" aria-label="Repositories" data-testid="gh-repo-list">
                        {filtered.map((r) => (
                          <li key={r.full_name}>
                            <button type="button" role="option" aria-selected={form.repo_full_name === r.full_name} disabled={!r.can_push}
                              onClick={() => set({ repo_full_name: r.full_name })} data-testid={`gh-repo-${r.full_name}`}
                              className={cn('flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted disabled:opacity-50',
                                form.repo_full_name === r.full_name && 'bg-muted font-semibold')}>
                              <RepoIcon size={16} className="shrink-0 text-slate" />
                              <span className="min-w-0 flex-1 truncate font-mono">{r.full_name}</span>
                              {r.private && <Lock className="size-3.5 text-muted-foreground" aria-label="private" />}
                              {!r.can_push && <span className="text-[11px] text-muted-foreground">read-only</span>}
                            </button>
                          </li>
                        ))}
                        {!filtered.length && <li className="px-3 py-2 text-[13px] text-muted-foreground">No repositories match.</li>}
                      </ul>
                    )}
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor="gh-branch" className="flex items-center gap-1.5 text-[13px] font-semibold"><GitBranchIcon size={16} /> Branch</Label>
                  <Input id="gh-branch" className={fieldCls} value={form.branch} onChange={(e) => set({ branch: e.target.value })} data-testid="gh-branch" />
                  <p className="text-xs text-muted-foreground">If the branch exists it must contain this job's history (fast-forward only).</p>
                </div>
                <div className="space-y-3 rounded-lg border border-border bg-card p-3.5">
                  <label className={cn('flex items-center gap-2.5 text-[13px] font-medium', mode === 'new' && 'opacity-60')}>
                    <input type="checkbox" className="size-4 accent-primary" checked={mode === 'existing' && form.open_pr} disabled={mode === 'new'}
                      onChange={(e) => set({ open_pr: e.target.checked })} data-testid="gh-open-pr" />
                    <GitPullRequestIcon size={16} /> Open a pull request
                  </label>
                  {mode === 'new' && <p className="text-xs text-muted-foreground" data-testid="gh-pr-new-hint">A new repository has no base branch yet - push first, then open PRs on later pushes.</p>}
                  {mode === 'existing' && form.open_pr && (
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="gh-pr-base" className="text-[12.5px] font-semibold">Base branch</Label>
                        <Input id="gh-pr-base" className={fieldCls} placeholder={selectedRepo?.default_branch || 'default branch'} value={form.pr_base} onChange={(e) => set({ pr_base: e.target.value })} data-testid="gh-pr-base" />
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="gh-pr-title" className="text-[12.5px] font-semibold">Title</Label>
                        <Input id="gh-pr-title" className="h-9 bg-card text-base sm:text-sm" value={form.pr_title} onChange={(e) => set({ pr_title: e.target.value })} data-testid="gh-pr-title" />
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="gh-pr-body" className="text-[12.5px] font-semibold">Description</Label>
                        <Textarea id="gh-pr-body" rows={7} className="bg-card font-mono text-[12.5px]" value={form.pr_body} onChange={(e) => set({ pr_body: e.target.value })} data-testid="gh-pr-body" />
                      </div>
                    </div>
                  )}
                </div>
                {error && <p role="alert" className="flex items-start gap-1.5 text-[13px] font-medium text-coral" data-testid="gh-push-error"><CircleAlert className="mt-0.5 size-4 shrink-0" /> {error}</p>}
                {result && <ResultLinks res={result} testId="gh-result" />}
                <Button onClick={submit} disabled={!canSubmit} className="w-full sm:w-auto" data-testid="gh-push">
                  <RepoPushIcon size={16} /> {busy ? 'Pushing...' : mode === 'existing' && form.open_pr ? 'Push and open PR' : 'Push'}
                </Button>
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
