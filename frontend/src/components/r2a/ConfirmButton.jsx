import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const ARM_MS = 4000

// Two-click confirm for destructive actions: the first click arms the button (label changes to
// `confirmLabel`, data-armed="true"), a second click within 4 s runs `onConfirm`. Clicking
// elsewhere (blur), Escape or the timeout disarms it.
export default function ConfirmButton({ onConfirm, children, confirmLabel = 'Click again to confirm', disabled, busy, testId, className, size = 'sm', title, ...rest }) {
  const [armed, setArmed] = useState(false)
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  const isArmed = armed && !disabled && !busy
  const disarm = () => { clearTimeout(timer.current); setArmed(false) }
  const onClick = () => {
    if (!isArmed) {
      setArmed(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setArmed(false), ARM_MS)
      return
    }
    disarm()
    onConfirm()
  }

  return (
    <Button
      size={size}
      variant="outline"
      onClick={onClick}
      onBlur={disarm}
      onKeyDown={(e) => { if (e.key === 'Escape') disarm() }}
      disabled={disabled || busy}
      data-testid={testId}
      data-armed={isArmed ? 'true' : 'false'}
      aria-live="polite"
      title={title}
      className={cn('text-coral hover:text-coral',
        isArmed ? 'border-coral bg-coral text-white hover:bg-coral/90 hover:text-white' : 'border-coral/40 hover:bg-coral-soft', className)}
      {...rest}
    >
      {isArmed ? confirmLabel : children}
    </Button>
  )
}
