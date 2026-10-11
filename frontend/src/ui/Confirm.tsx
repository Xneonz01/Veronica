// "Are you sure?", asked properly: never window.confirm. Cancel takes the focus
// first, because the keyboard's first instinct must not be the irreversible one.
// While the action runs the sheet can't be dismissed, and a failure stays open
// with its reason.

import { useRef, type ReactNode } from 'react'
import { Button } from './Button'
import styles from './Confirm.module.css'
import { Sheet } from './Sheet'

export type ConfirmProps = {
  open: boolean
  title: ReactNode
  children?: ReactNode
  confirmLabel: string
  /** The way out keeps things as they are: "Keep it", "Not now". */
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
  error?: string | null
  destructive?: boolean
}

export function Confirm({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  busy = false,
  error,
  destructive = false,
}: ConfirmProps) {
  const cancel = useRef<HTMLButtonElement>(null)
  return (
    <Sheet
      open={open}
      onClose={busy ? () => undefined : onCancel}
      title={title}
      size="sm"
      initialFocus={cancel}
      footer={
        <>
          <Button ref={cancel} variant="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant={destructive ? 'danger' : 'primary'} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children != null && <p className={styles.body}>{children}</p>}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </Sheet>
  )
}
