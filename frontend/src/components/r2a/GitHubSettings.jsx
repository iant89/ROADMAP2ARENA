import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { CircleAlert, Copy, ExternalLink, KeyRound, LockKeyhole, LogIn, Unplug, X } from 'lucide-react'
import { CheckCircleIcon, DotFillIcon, GitPullRequestIcon, MarkGithubIcon, RepoPushIcon, SyncIcon, XCircleIcon } from '@primer/octicons-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { cancelGitHubOAuth, connectGitHub, disconnectGitHub, getGitHub, getGitHubWatches, pollGitHubNow, pollGitHubOAuth, saveGitHubSettings, startGitHubOAuth } from '@/lib/api'
import { cn } from '@/lib/utils'
import ConfirmButton from './ConfirmButton'
import { formatDateTime } from './status'

const KEY_CMD = '/app/venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'

function CiBadge({ state }) {
  if (state === 'success') return <span className="inline-flex items-center gap-1 text-teal"><CheckCircleIcon size={12} /> checks passed</span>
  if (state === 'failure') return <span className="inline-flex items-center gap-1 text-coral"><XCircleIcon size={12} /> checks failed</span>
  if (state === 'pending') return <span className="inline-flex items-center gap-1 text-pause"><DotFillIcon size={12} /> checks running</span>
  return <span>no checks yet</span>
}

// Pushed branches the backend polls (branch, PR, CI) to raise github_* notifications.
function GitHubWatches() {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => getGitHubWatches().then(setData).catch(() => setData(null)), [])
  useEffect(() => { load() }, [load])
  const pollNow = async () => {
    setBusy(true)
    try {
      const res = await pollGitHubNow()
      if (res.skipped) toast.info('GitHub check skipped', { description: res.skipped === 'not_connected' ? 'GitHub is not connected.' : `Paused (${res.skipped.replace('_', ' ')}) - resumes in ${res.resume_in}s.` })
      else toast.success('Checked GitHub', { description: `${res.watches} branch${res.watches === 1 ? '' : 'es'} checked, ${res.notifications} new notification${res.notifications === 1 ? '' : 's'}.` })
    } catch (err) {
      toast.error('GitHub check failed', { description: err.message })
    } finally {
      setBusy(false)
      load()
    }
  }
  if (!data) return null
  const active = data.items.filter((w) => w.active)
  return (
    <div className="space-y-2 border-t border-border pt-4" data-testid="gh-watches">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-[13px] font-semibold"><SyncIcon size={16} /> Watching pushed branches</p>
        <Button size="xs" variant="outline" onClick={pollNow} disabled={busy} data-testid="gh-poll-now"><SyncIcon size={16} className={cn(busy && 'animate-spin')} /> Check now</Button>
      </div>
      <p className="text-xs text-muted-foreground" data-testid="gh-watches-summary">
        {active.length} active - checked every {data.poll_seconds}s for new commits, pull request merges and CI results (for 7 days after a push).
        {data.paused_for > 0 && <span className="text-coral"> Paused for {data.paused_for}s ({(data.pause_reason || '').replace('_', ' ')}).</span>}
      </p>
      {active.length > 0 && (
        <ul className="space-y-1 text-xs" data-testid="gh-watch-list">
          {active.slice(0, 6).map((w) => (
            <li key={`${w.full_name}:${w.branch}:${w.job_id}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11.5px]">
              <a href={`${w.html_url}/tree/${w.branch}`} target="_blank" rel="noreferrer" className="truncate underline underline-offset-2">{w.full_name}:{w.branch}</a>
              {w.pr_number && <span className="inline-flex items-center gap-1"><GitPullRequestIcon size={12} /> #{w.pr_number} {w.state?.pr_state}</span>}
              <span className="text-muted-foreground"><CiBadge state={w.state?.ci} /></span>
              {w.last_error && <span className="text-coral">{w.last_error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Settings > GitHub: connect with OAuth (device flow) OR a personal access token - mutually
// exclusive, the backend enforces it. The token is stored on the backend only, Fernet-encrypted
// with R2A_SECRET_KEY, and never shown. R2A_GITHUB_TOKEN in backend/.env overrides both.
// Also disconnect, the auto-push-on-completion option and the branch watcher.
export default function GitHubSettings() {
  const [gh, setGh] = useState(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const [pending, setPending] = useState(null) // device flow: { user_code, verification_uri, expires_at, interval }
  const [clientId, setClientId] = useState('')
  const timer = useRef(null)
  const pollRef = useRef(null) // the poll callback re-schedules itself through this ref

  useEffect(() => {
    getGitHub().then((g) => { setGh(g); setPending(g.oauth?.pending || null) }).catch((err) => setError(err.message))
    return () => clearTimeout(timer.current)
  }, [])

  // Poll the backend (which polls GitHub) every `interval` seconds while a device code is pending.
  const poll = useCallback(async (interval) => {
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const res = await pollGitHubOAuth()
        if (res.status === 'connected') {
          setPending(null)
          setGh(res.github)
          toast.success('GitHub connected', { description: `Signed in as @${res.github.username} (OAuth)` })
        } else if (res.status === 'pending' || res.status === 'slow_down') {
          pollRef.current?.(res.interval)
        } else {
          setPending(null)
          setGh(res.github)
          setError(res.status === 'expired' ? 'The code expired before it was entered - start again.' : 'Authorization was denied on GitHub.')
        }
      } catch (err) {
        setPending(null)
        setError(err.message)
      }
    }, Math.max(1, interval || 5) * 1000)
  }, [])

  useEffect(() => { pollRef.current = poll }, [poll])
  useEffect(() => {
    if (pending) poll(pending.interval)
  }, [pending, poll])

  const startOAuth = async () => {
    setBusy(true)
    setError(null)
    try {
      setPending(await startGitHubOAuth())
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  const cancelOAuth = async () => {
    clearTimeout(timer.current)
    setPending(null)
    try { await cancelGitHubOAuth() } catch { /* already gone */ }
  }
  const saveClientId = async (value) => {
    try {
      setGh(await saveGitHubSettings({ oauth_client_id: value }))
      setClientId('')
      toast.success(value ? 'OAuth client ID saved' : 'OAuth client ID cleared')
    } catch (err) {
      setError(err.message)
    }
  }

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await connectGitHub(token.trim())
      setGh(res)
      setToken('')
      toast.success('GitHub connected', { description: `Signed in as @${res.username}` })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  const disconnect = async () => {
    setBusy(true)
    try {
      setGh(await disconnectGitHub())
      toast.success('GitHub disconnected', { description: 'The token was removed from the backend.' })
    } catch (err) {
      toast.error('Could not disconnect', { description: err.message })
    } finally {
      setBusy(false)
    }
  }
  // Optimistic toggle: flip at once, revert if the backend rejects it.
  const setAuto = async (patch) => {
    const before = gh
    setGh((g) => ({ ...g, auto_push: { ...g.auto_push, ...patch } }))
    try {
      setGh(await saveGitHubSettings({ auto_push: patch }))
    } catch (err) {
      setGh(before)
      toast.error('Could not save the auto-push setting', { description: err.message })
    }
  }

  const envToken = gh?.auth_method === 'env'
  const noKey = Boolean(gh) && !envToken && gh.encryption !== 'ok'

  return (
    <section className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm lg:p-6" data-testid="github-settings">
      <div>
        <h3 className="flex items-center gap-2 text-[15px] font-semibold"><MarkGithubIcon size={16} /> GitHub</h3>
        <p className="text-[13px] text-muted-foreground">Push a job's git history to a GitHub repository and open pull requests.</p>
      </div>
      {!gh && !error && <Skeleton className="h-20 rounded-lg" />}
      {noKey && (
        <div role="alert" className="space-y-1.5 rounded-lg border border-coral/40 bg-coral/5 p-3 text-xs" data-testid="gh-key-missing">
          <p className="flex items-center gap-1.5 font-semibold text-coral"><LockKeyhole className="size-3.5" /> {gh.encryption === 'invalid' ? 'R2A_SECRET_KEY is not a valid Fernet key' : 'R2A_SECRET_KEY is not set'}</p>
          <p className="text-muted-foreground">The GitHub token is stored encrypted. Add a key to <code>backend/.env</code> and restart the backend:</p>
          <code className="block overflow-x-auto whitespace-nowrap rounded bg-muted px-2 py-1">{KEY_CMD}</code>
        </div>
      )}
      {gh?.token_error && <p role="alert" className="flex items-center gap-1.5 text-xs font-medium text-coral" data-testid="gh-token-error"><CircleAlert className="size-3.5" /> {gh.token_error}</p>}
      {envToken && (
        <p className="flex items-center gap-1.5 rounded-lg border border-border bg-paper px-3 py-2 text-xs text-muted-foreground" data-testid="gh-env-token">
          <KeyRound className="size-3.5" /> Using <code>R2A_GITHUB_TOKEN</code> from backend/.env - it overrides OAuth and tokens saved here. Remove it there (and restart) to manage the connection in this page.
        </p>
      )}
      {gh && (
        <div className="grid gap-3 lg:grid-cols-2">
          {/* OAuth device flow */}
          <div className={cn('space-y-2.5 rounded-lg border border-border p-3.5', (gh.auth_method === 'pat' || envToken) && 'opacity-60')} data-testid="gh-oauth-card">
            <p className="flex items-center gap-2 text-[13px] font-semibold"><LogIn className="size-4" /> Sign in with GitHub (OAuth)</p>
            {gh.auth_method === 'pat' && <p className="text-xs text-muted-foreground" data-testid="gh-oauth-disabled-note">Connected with a personal access token - disconnect it first to use OAuth.</p>}
            {gh.auth_method === 'oauth' && <p className="text-xs font-medium text-teal" data-testid="gh-oauth-active">Connected with OAuth.</p>}
            {!gh.connected && !pending && !envToken && (
              gh.oauth.available ? (
                <>
                  <p className="text-xs text-muted-foreground">Device flow: GitHub shows a page where you enter a short code - no callback URL needed. Requests the <code>repo</code> scope.</p>
                  <Button size="sm" onClick={startOAuth} disabled={busy || noKey} data-testid="gh-oauth-start"><MarkGithubIcon size={16} /> Sign in with GitHub</Button>
                  <p className="text-[11px] text-muted-foreground">OAuth app client ID <code>{gh.oauth.client_id}</code> ({gh.oauth.client_id_source === 'settings' ? 'saved here' : 'from backend/.env'}){gh.oauth.client_id_source === 'settings' && <> - <button type="button" className="underline" onClick={() => saveClientId('')}>clear</button></>}</p>
                </>
              ) : (
                <div className="space-y-2" data-testid="gh-oauth-unconfigured">
                  <p className="text-xs text-muted-foreground">Needs a GitHub OAuth app with device flow enabled. Set <code>GITHUB_OAUTH_CLIENT_ID</code> in backend/.env or enter the client ID here.</p>
                  <div className="flex gap-2">
                    <Input className="h-8 bg-card font-mono text-base sm:text-[12.5px]" placeholder="Ov23li..." value={clientId} onChange={(e) => setClientId(e.target.value)} data-testid="gh-oauth-client-id" />
                    <Button size="sm" variant="outline" disabled={!clientId.trim()} onClick={() => saveClientId(clientId.trim())} data-testid="gh-oauth-client-id-save">Save</Button>
                  </div>
                </div>
              )
            )}
            {pending && (
              <div className="space-y-2 rounded-md bg-paper p-3" data-testid="gh-oauth-pending">
                <p className="text-xs text-muted-foreground">Open the page and enter this code:</p>
                <div className="flex items-center gap-2">
                  <code className="rounded-md border border-border bg-card px-3 py-1.5 text-lg font-semibold tracking-[0.2em]" data-testid="gh-oauth-code">{pending.user_code}</code>
                  <Button size="icon-sm" variant="ghost" aria-label="Copy code" title="Copy code" onClick={() => navigator.clipboard?.writeText(pending.user_code).then(() => toast.success('Code copied'))}><Copy /></Button>
                </div>
                <a href={pending.verification_uri} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-[13px] font-medium underline underline-offset-2" data-testid="gh-oauth-url">
                  {pending.verification_uri} <ExternalLink className="size-3.5" />
                </a>
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="size-2 animate-pulse rounded-full bg-teal" /> Waiting for authorization...
                  <Button size="xs" variant="ghost" onClick={cancelOAuth} data-testid="gh-oauth-cancel"><X /> Cancel</Button>
                </p>
              </div>
            )}
          </div>
          {/* Personal access token */}
          <div className={cn('space-y-2.5 rounded-lg border border-border p-3.5', (gh.auth_method === 'oauth' || pending || envToken) && 'opacity-60')} data-testid="gh-pat-card">
            <Label htmlFor="gh-token" className="flex items-center gap-2 text-[13px] font-semibold"><KeyRound className="size-4" /> Personal access token</Label>
            {(gh.auth_method === 'oauth' || pending) && <p className="text-xs text-muted-foreground" data-testid="gh-pat-disabled-note">{pending ? 'Finish or cancel the OAuth sign-in first.' : 'Connected with OAuth - disconnect it first to use a token.'}</p>}
            {gh.auth_method === 'pat' && <p className="text-xs font-medium text-teal" data-testid="gh-pat-active">Connected with a personal access token.</p>}
            {!gh.connected && !envToken && (
              <>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input id="gh-token" type="password" autoComplete="off" spellCheck={false} placeholder="ghp_... or github_pat_..." disabled={Boolean(pending)}
                    className="h-9 bg-card font-mono text-base sm:text-sm" value={token} onChange={(e) => setToken(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && token.trim()) connect() }} data-testid="gh-token-input" />
                  <Button onClick={connect} disabled={busy || !token.trim() || Boolean(pending) || noKey} data-testid="gh-connect">
                    <KeyRound /> {busy ? 'Checking...' : 'Connect'}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Stored on the backend only, encrypted with R2A_SECRET_KEY, and never shown again. Classic: <code>repo</code> scope (or <code>public_repo</code>). Fine-grained: Contents and Pull requests read/write, plus Administration to create repositories.
                </p>
              </>
            )}
          </div>
        </div>
      )}
      {gh?.connected && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-paper px-3.5 py-3">
            {gh.avatar_url && <img src={gh.avatar_url} alt="" className="size-9 rounded-full border border-border" />}
            <div className="min-w-0 flex-1">
              <p className="text-[13.5px] font-semibold">Connected as <a href={gh.html_url} target="_blank" rel="noreferrer" className="underline underline-offset-2" data-testid="gh-username">@{gh.username}</a></p>
              <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" data-testid="gh-scopes">
                <KeyRound className="size-3.5" /> {envToken ? 'Token from backend/.env' : gh.auth_method === 'oauth' ? 'OAuth (device flow)' : gh.token_type === 'fine-grained' ? 'Fine-grained token' : 'Classic token'}
                {!envToken && <span className="inline-flex items-center gap-1" data-testid="gh-encrypted"><LockKeyhole className="size-3" /> encrypted at rest</span>}
                {gh.scopes.length > 0 && <> - scopes: {gh.scopes.map((s) => <code key={s} className="rounded bg-muted px-1">{s}</code>)}</>}
                {gh.connected_at && <> - since {formatDateTime(gh.connected_at)}</>}
              </p>
              {gh.token_type === 'classic' && !gh.scopes.some((s) => s === 'repo' || s === 'public_repo') && (
                <p className="mt-1 flex items-center gap-1.5 text-xs font-medium text-coral" data-testid="gh-scope-warning"><CircleAlert className="size-3.5" /> This token has no repo scope - pushing will fail.</p>
              )}
            </div>
            {!envToken && (
              <ConfirmButton onConfirm={disconnect} busy={busy} testId="gh-disconnect" confirmLabel={<><Unplug /> Remove token?</>}>
                <Unplug /> Disconnect
              </ConfirmButton>
            )}
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2.5 text-[13px] font-medium">
              <input type="checkbox" className="size-4 accent-primary" checked={gh.auto_push.enabled}
                onChange={(e) => setAuto({ enabled: e.target.checked })} data-testid="gh-autopush-enabled" />
              <RepoPushIcon size={16} /> Push automatically when a job completes
            </label>
            <label className="ml-6.5 flex items-center gap-2.5 text-[13px]">
              <input type="checkbox" className="size-4 accent-primary" checked={gh.auto_push.private} disabled={!gh.auto_push.enabled}
                onChange={(e) => setAuto({ private: e.target.checked })} data-testid="gh-autopush-private" />
              New repositories are private
            </label>
            <p className="ml-6.5 text-xs text-muted-foreground">A job that was pushed before goes to the same repository and branch; otherwise a new repository is created. Failures are written to the job log.</p>
          </div>
          <GitHubWatches />
        </div>
      )}
      {error && <p role="alert" className="flex items-center gap-1.5 text-xs font-medium text-coral" data-testid="gh-error"><CircleAlert className="size-3.5" /> {error}</p>}
    </section>
  )
}
