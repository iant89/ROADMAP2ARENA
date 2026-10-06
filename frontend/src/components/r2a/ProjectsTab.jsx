import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, FolderOpen, LockKeyhole, Pencil, Plus, RefreshCw, Rocket, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import ConfirmButton from './ConfirmButton'
import { createProject, deleteProject, getGitHub, listGitHubRepos, refreshProject, updateProject } from '@/lib/api'
import { refreshProjects, useProjects } from '@/hooks/useProjects'
import { formatDateTime } from './status'

const fieldClass = 'font-mono text-base sm:text-sm'

async function fetchGitHubProjectOptions() {
  const info = await getGitHub()
  const repositories = info.connected ? await listGitHubRepos() : { items: [] }
  return { info, items: repositories.items }
}

function ProjectCard({ project, githubConnected, onUse, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(project.name)
  const [context, setContext] = useState(project.context || '')
  const [busy, setBusy] = useState(null)

  const save = async () => {
    setBusy('save')
    const nextName = name.trim()
    const nextContext = context.trim()
    try {
      await updateProject(project.id, { name: nextName, context: nextContext })
      setName(nextName)
      setContext(nextContext)
      setEditing(false)
      await onChanged()
      toast.success('Project updated', { description: project.name })
    } catch (err) {
      toast.error('Could not update project', { description: err.message })
    } finally {
      setBusy(null)
    }
  }

  const refresh = async () => {
    setBusy('refresh')
    try {
      const next = await refreshProject(project.id)
      await onChanged()
      toast.success('Project source refreshed', { description: `${next.source.repo_full_name} at ${next.head?.slice(0, 7) || 'empty'}` })
    } catch (err) {
      toast.error('Could not refresh project', { description: err.message })
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    setBusy('delete')
    try {
      await deleteProject(project.id)
      await onChanged()
      toast.success('Project deleted', { description: project.name })
    } catch (err) {
      toast.error('Could not delete project', { description: err.message })
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className="space-y-3 rounded-xl border border-border bg-card p-4 shadow-sm" data-testid={`project-card-${project.id}`}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary text-foreground"><FolderOpen className="size-4" /></div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[15px] font-semibold" data-testid="project-name">{project.name}</h3>
          <a className="inline-flex max-w-full items-center gap-1 font-mono text-xs text-muted-foreground underline-offset-2 hover:underline"
            href={project.source.html_url} target="_blank" rel="noreferrer" data-testid="project-source-link">
            <span className="truncate">{project.source.repo_full_name}</span>
            {project.source.private && <LockKeyhole className="size-3 shrink-0" aria-label="Private repository" />}
            <ExternalLink className="size-3 shrink-0" />
          </a>
          <p className="mt-1 text-xs text-muted-foreground">
            Branch <span className="font-mono">{project.source.branch}</span>
            {' · '}source <span className="font-mono">{project.head?.slice(0, 7) || 'no commit'}</span>
            {' · '}{project.job_count} job{project.job_count === 1 ? '' : 's'}
          </p>
        </div>
        <Button size="sm" onClick={() => onUse(project)} data-testid={`project-use-${project.id}`}><Rocket /> Use project</Button>
      </div>

      {editing ? (
        <div className="space-y-3 border-t border-border pt-3" data-testid="project-edit-form">
          <div className="space-y-1.5">
            <Label htmlFor={`project-name-${project.id}`} className="text-[13px] font-semibold">Project name</Label>
            <Input id={`project-name-${project.id}`} className={fieldClass} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} data-testid="project-edit-name" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`project-context-${project.id}`} className="text-[13px] font-semibold">Saved project instructions</Label>
            <Textarea id={`project-context-${project.id}`} className={`${fieldClass} min-h-24 resize-y bg-paper`} value={context} maxLength={20000}
              onChange={(e) => setContext(e.target.value)} data-testid="project-edit-context" />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={save} disabled={!name.trim() || busy} data-testid="project-save"><Pencil /> {busy === 'save' ? 'Saving...' : 'Save changes'}</Button>
            <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setName(project.name); setContext(project.context || '') }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <>
          {project.context && <p className="line-clamp-3 whitespace-pre-wrap text-[13px] text-muted-foreground" data-testid="project-context-preview">{project.context}</p>}
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button size="sm" variant="outline" onClick={() => { setName(project.name); setContext(project.context || ''); setEditing(true) }} data-testid={`project-edit-${project.id}`}><Pencil /> Edit instructions</Button>
            <Button size="sm" variant="outline" onClick={refresh} disabled={!githubConnected || !!busy} title={!githubConnected ? 'Connect GitHub in Settings to refresh' : 'Import the latest commit from this branch'} data-testid={`project-refresh-${project.id}`}>
              <RefreshCw className={busy === 'refresh' ? 'animate-spin' : ''} /> {busy === 'refresh' ? 'Refreshing...' : 'Refresh source'}
            </Button>
            <ConfirmButton
              onConfirm={remove}
              busy={busy === 'delete'}
              disabled={project.job_count > 0 || !!busy}
              testId={`project-delete-${project.id}`}
              confirmLabel="Delete project?"
              title={project.job_count > 0 ? 'Delete linked jobs first to preserve their project history' : 'Delete this imported project'}
            ><Trash2 /> {busy === 'delete' ? 'Deleting...' : 'Delete'}</ConfirmButton>
            {project.job_count > 0 && <span className="text-xs text-muted-foreground">Delete linked jobs before removing this project.</span>}
          </div>
          <p className="text-[11px] text-muted-foreground" data-testid="project-updated-at">
            Imported {formatDateTime(project.created_at)}{project.source.remote_updated_at ? ` · GitHub updated ${formatDateTime(project.source.remote_updated_at)}` : ''}
          </p>
        </>
      )}
    </article>
  )
}

export default function ProjectsTab({ onUseProject, onOpenSettings }) {
  const { data: projects, error: projectsError, refresh } = useProjects()
  const [github, setGithub] = useState(null)
  const [githubError, setGithubError] = useState(null)
  const [repositories, setRepositories] = useState(null)
  const [repositoriesError, setRepositoriesError] = useState(null)
  const [query, setQuery] = useState('')
  const [form, setForm] = useState({ repo_full_name: '', name: '', context: '' })
  const [busy, setBusy] = useState(false)

  const loadGitHub = async () => {
    setGithubError(null)
    setRepositoriesError(null)
    try {
      const result = await fetchGitHubProjectOptions()
      setGithub(result.info)
      setRepositories(result.items)
    } catch (err) {
      setGithubError(err.message)
      setRepositories(null)
    }
  }

  useEffect(() => {
    let alive = true
    fetchGitHubProjectOptions()
      .then((result) => {
        if (!alive) return
        setGithub(result.info)
        setRepositories(result.items)
      })
      .catch((err) => {
        if (!alive) return
        setGithubError(err.message)
        setRepositories(null)
      })
    return () => { alive = false }
  }, [])

  const filteredRepos = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (repositories || []).filter((repo) => !needle || repo.full_name.toLowerCase().includes(needle)).slice(0, 100)
  }, [repositories, query])
  const selectedRepo = (repositories || []).find((repo) => repo.full_name === form.repo_full_name)

  const selectRepo = (fullName) => {
    const next = (repositories || []).find((repo) => repo.full_name === fullName)
    const previous = selectedRepo
    setForm((current) => ({
      ...current,
      repo_full_name: fullName,
      name: !current.name || current.name === previous?.name ? (next?.name || '') : current.name,
    }))
  }

  const submit = async () => {
    if (!form.repo_full_name) return
    setBusy(true)
    try {
      const project = await createProject({
        repo_full_name: form.repo_full_name,
        name: form.name.trim() || undefined,
        context: form.context.trim(),
      })
      setForm({ repo_full_name: '', name: '', context: '' })
      await refreshProjects()
      toast.success('Project imported', { description: `${project.name} · ${project.source.repo_full_name} at ${project.head.slice(0, 7)}` })
    } catch (err) {
      toast.error('Could not import project', { description: err.message })
    } finally {
      setBusy(false)
    }
  }

  const useProject = (project) => {
    onUseProject?.(project)
    toast.success('Project selected', { description: `${project.name} · ${project.source.repo_full_name}` })
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-5 lg:p-7" data-testid="projects-tab">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight"><FolderOpen className="size-5" /> Projects</h2>
        <p className="mt-1 text-[13px] text-muted-foreground">Import a GitHub repository once, keep project instructions, and start each job from a private copy of the selected source revision.</p>
      </div>

      <section className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6" data-testid="project-import-form">
        <div>
          <h3 className="flex items-center gap-2 text-[15px] font-semibold"><Plus className="size-4" /> Import a repository</h3>
          <p className="mt-1 text-xs text-muted-foreground">Uses your GitHub connection. Imports the default branch; credentials are never stored in the Git remote.</p>
        </div>

        {githubError && <p className="rounded-lg border border-coral/30 bg-coral-soft p-3 text-sm text-coral" data-testid="project-github-error">GitHub connection could not be checked: {githubError}</p>}
        {github && !github.connected && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-paper p-3.5 text-[13px]" data-testid="project-github-disconnected">
            <span>Connect GitHub to import a private or public repository.</span>
            {onOpenSettings && <Button size="sm" variant="outline" onClick={onOpenSettings} data-testid="project-open-github-settings">Open GitHub settings</Button>}
            <Button size="sm" variant="ghost" onClick={loadGitHub} data-testid="project-check-github"><RefreshCw /> Check again</Button>
          </div>
        )}
        {!github && !githubError && <Skeleton className="h-14 rounded-lg" />}
        {github?.connected && (
          <>
            {repositoriesError && <p className="text-sm text-coral" data-testid="project-repositories-error">Could not load repositories: {repositoriesError}</p>}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="project-source-repo" className="text-[13px] font-semibold">GitHub repository</Label>
                <Input id="project-repo-search" className={fieldClass} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter repositories" data-testid="project-repo-search" />
                <select id="project-source-repo" className="h-9 w-full rounded-lg border border-input bg-paper px-2.5 text-base sm:text-sm" value={form.repo_full_name}
                  onChange={(e) => selectRepo(e.target.value)} disabled={!repositories || busy} data-testid="project-repo-select">
                  <option value="">Select a repository...</option>
                  {filteredRepos.map((repo) => <option key={repo.full_name} value={repo.full_name}>{repo.full_name}{repo.private ? ' · private' : ''}</option>)}
                </select>
                {repositories && !filteredRepos.length && <p className="text-xs text-muted-foreground">No repositories match.</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="project-import-name" className="text-[13px] font-semibold">Project name</Label>
                <Input id="project-import-name" className={fieldClass} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder={selectedRepo?.name || 'My project'} maxLength={100} data-testid="project-import-name" />
                {selectedRepo && <p className="text-xs text-muted-foreground">Branch <span className="font-mono">{selectedRepo.default_branch || 'unknown'}</span></p>}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-import-context" className="text-[13px] font-semibold">Saved project instructions</Label>
              <Textarea id="project-import-context" className={`${fieldClass} min-h-24 resize-y bg-paper`} value={form.context} onChange={(e) => setForm((f) => ({ ...f, context: e.target.value }))}
                placeholder="Stack, architecture, constraints, conventions, commands..." maxLength={20000} data-testid="project-import-context" />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button size="lg" onClick={submit} disabled={!form.repo_full_name || !form.name.trim() || busy} data-testid="project-import-button">
                <Plus /> {busy ? 'Importing...' : 'Import project'}
              </Button>
              <span className="text-xs text-muted-foreground">Imports repositories up to 100 MB. Source files sent to a model are bounded and credential-like files are excluded by filename.</span>
            </div>
          </>
        )}
      </section>

      <section className="space-y-3" data-testid="project-list">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-[15px] font-semibold">Your projects</h3>
          <Button size="xs" variant="ghost" onClick={refresh} data-testid="projects-refresh"><RefreshCw /> Refresh list</Button>
        </div>
        {projectsError && <p className="rounded-lg border border-coral/30 bg-coral-soft p-3 text-sm text-coral" data-testid="projects-load-error">Could not load projects: {projectsError}</p>}
        {projects === null && !projectsError && [0, 1].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
        {projects?.length === 0 && <div className="rounded-xl border border-dashed border-border bg-paper p-8 text-center text-[13px] text-muted-foreground" data-testid="projects-empty">No projects yet. Import a GitHub repository to start from an existing codebase.</div>}
        {projects?.map((project) => <ProjectCard key={project.id} project={project} githubConnected={Boolean(github?.connected)} onUse={useProject} onChanged={refresh} />)}
      </section>
    </div>
  )
}
