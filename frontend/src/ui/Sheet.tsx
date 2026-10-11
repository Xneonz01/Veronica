// The one modal surface. A native <dialog> opened with showModal() gives the
// focus trap, the inert background and the top layer for free. Escape arrives as
// a `cancel` event, which is handed to the owner instead of closing on its own,
// so the owner's state stays the truth. The browser may still close it (a second
// Escape it won't let a page refuse): the owner hears of it, and one that keeps it
// open (a recovery key not yet saved, an action still running) has it shown again.

import { X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react'
import { cx } from './cx'
import styles from './Sheet.module.css'

export type SheetProps = {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  footer?: ReactNode
  /** 400 / 520 / 680 px; a bottom sheet in a window narrower than 768 px. */
  size?: 'sm' | 'md' | 'lg'
  /** Keep the title for screen readers only. */
  bareTitle?: boolean
  /** Where the keyboard lands on opening. Without it, the first control does. */
  initialFocus?: RefObject<HTMLElement | null>
  className?: string
}

export function Sheet({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  bareTitle = false,
  initialFocus,
  className,
}: SheetProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const pressedBackdrop = useRef(false)
  const titleId = useId()
  const descriptionId = useId()
  // Each close the browser made on its own: the owner has answered it by the next
  // render, and if it still says open, the dialog is shown again.
  const [closedByBrowser, setClosedByBrowser] = useState(0)

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) {
      element.showModal()
      initialFocus?.current?.focus()
    }
    if (!open && element.open) element.close()
  }, [open, initialFocus, closedByBrowser])

  return (
    <dialog
      ref={dialog}
      className={cx(styles.dialog, styles[size], className)}
      aria-labelledby={titleId}
      aria-describedby={description != null ? descriptionId : undefined}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      // The browser can close a dialog without asking (a form with
      // method="dialog", or a second Escape it won't let a page refuse); the
      // owner hears about it so its state doesn't say "open" over nothing.
      onClose={() => {
        if (!open) return
        onClose()
        setClosedByBrowser((count) => count + 1)
      }}
      // A press on the dialog element itself is a press on the backdrop: the
      // panel fills the dialog, so anything inside it has a deeper target. Both
      // ends of the click are checked, so a text selection dragged out of the
      // panel doesn't close it.
      onPointerDown={(event) => {
        pressedBackdrop.current = event.target === event.currentTarget
      }}
      onClick={(event) => {
        if (pressedBackdrop.current && event.target === event.currentTarget) onClose()
        pressedBackdrop.current = false
      }}
    >
      <div className={styles.panel}>
        <span className={styles.grabber} aria-hidden="true" />
        <header className={cx(styles.header, bareTitle && styles.headerBare)}>
          <div className={styles.titles}>
            <h2 id={titleId} className={bareTitle ? 'visually-hidden' : styles.title}>
              {title}
            </h2>
            {description != null && (
              <p id={descriptionId} className={styles.description}>
                {description}
              </p>
            )}
          </div>
          <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className={styles.body}>{children}</div>
        {footer != null && <footer className={styles.footer}>{footer}</footer>}
      </div>
    </dialog>
  )
}
