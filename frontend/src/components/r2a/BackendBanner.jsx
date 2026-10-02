import { RefreshCw, ServerCrash } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function BackendBanner({ message, url, onRetry }) {
  if (!message) return null
  return (
    <div className="r2a-rise flex flex-wrap items-center gap-3 border-b border-coral/30 bg-coral-soft px-5 py-2.5 text-[13px] text-coral lg:px-8" role="alert" data-testid="backend-error-banner">
      <ServerCrash className="size-4 shrink-0" />
      <span className="font-medium">Backend problem:</span>
      <span className="min-w-0 flex-1 text-foreground/85">{message} <span className="font-mono text-xs text-muted-foreground">({url})</span></span>
      <Button size="sm" variant="outline" onClick={onRetry} data-testid="backend-retry-button">
        <RefreshCw /> Retry
      </Button>
    </div>
  )
}
