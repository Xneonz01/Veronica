// One stack of transient messages at the window's corner: what she remembered,
// with Undo while it can still be undone, and the outcome of actions taken away
// from where they show. Pointing at or focusing a toast holds it.

import { CircleAlert, CircleCheck, Info, X } from 'lucide-react'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import type { MemoryAction, Notification } from '../notify/types'
import { useNotifications } from '../notify/useNotifications'
import { Countdown } from '../ui/Countdown'
import { cx } from '../ui/cx'
import { Mark } from '../ui/Mark'
import { ToastContext, type ToastInput, type ToastTone } from './toast'
import styles from './Toasts.module.css'
import { useExpiry } from './useExpiry'

const MEMORY_MS = 6000
const LEARNED_MS = 8000
const TOAST_MS = { info: 6000, positive: 6000, critical: 10000 } satisfies Record<ToastTone, number>
const MAX_APP_TOASTS = 3

type AppToast = ToastInput & { id: string }
let counter = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<AppToast[]>([])
  const show = useCallback((toast: ToastInput) => {
    setItems((current) => [...current, { ...toast, id: `toast-${++counter}` }].slice(-MAX_APP_TOASTS))
  }, [])
  const dismiss = useCallback((id: string) => setItems((current) => current.filter((item) => item.id !== id)), [])
  const toaster = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={toaster}>
      {children}
      <Stack items={items} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

function Stack({ items, onDismiss }: { items: AppToast[]; onDismiss: (id: string) => void }) {
  const { toasts: memories, dismiss, undo } = useNotifications()
  return (
    <section className={styles.stack} aria-label="Notifications" aria-live="polite">
      {memories.map((notification) =>
        notification.members.length > 0 ? (
          <LearnedToast key={notification.id} notification={notification} onDismiss={dismiss} />
        ) : (
          <MemoryToast key={notification.id} notification={notification} onDismiss={dismiss} onUndo={undo} />
        ),
      )}
      {items.map((toast) => (
        <AppToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </section>
  )
}

// What she says when she writes to her memory, by what happened and who asked.
function memoryLine(notification: Notification): string {
  const lines: Record<MemoryAction, string> = {
    added: notification.source === 'user' ? "I'll remember that" : 'I picked that up',
    candidate: 'Worth keeping?',
    superseded: 'Updated what I knew',
    updated: 'Noted',
    removed: 'Forgotten',
  }
  return lines[notification.action]
}

function MemoryToast({
  notification,
  onDismiss,
  onUndo,
}: {
  notification: Notification
  onDismiss: (id: string) => void
  onUndo: (notification: Notification) => void
}) {
  const { paused, holders } = useExpiry(MEMORY_MS, () => onDismiss(notification.id))
  return (
    <div className={styles.toast} {...holders}>
      <span className={styles.mark}>
        <Mark size={18} />
      </span>
      <div className={styles.text}>
        <span className={styles.title}>{memoryLine(notification)}</span>
        <span className={cx(styles.body, notification.source !== 'user' && styles.inferred)}>
          {notification.summary}
        </span>
      </div>
      {notification.undoable ? (
        <button type="button" className={styles.undo} onClick={() => onUndo(notification)}>
          <Countdown durationMs={MEMORY_MS} paused={paused} size={14} />
          <span>Undo</span>
        </button>
      ) : (
        <Dismiss onClick={() => onDismiss(notification.id)} />
      )}
    </div>
  )
}

function LearnedToast({ notification, onDismiss }: { notification: Notification; onDismiss: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const items = [notification, ...notification.members]
  // Opened, it stays until closed: they are reading it.
  const { holders } = useExpiry(LEARNED_MS, () => {
    if (!open) onDismiss(notification.id)
  })
  return (
    <div className={styles.toast} {...holders}>
      <span className={styles.mark}>
        <Mark size={18} />
      </span>
      <div className={styles.text}>
        <button type="button" className={styles.expand} aria-expanded={open} onClick={() => setOpen((was) => !was)}>
          I learned {items.length} things
        </button>
        {open && (
          <ul className={styles.learned}>
            {items.map((item) => (
              <li key={item.id}>{item.summary}</li>
            ))}
          </ul>
        )}
      </div>
      <Dismiss onClick={() => onDismiss(notification.id)} />
    </div>
  )
}

const TONE_ICON = { info: Info, positive: CircleCheck, critical: CircleAlert } satisfies Record<ToastTone, unknown>

function AppToastCard({ toast, onDismiss }: { toast: AppToast; onDismiss: (id: string) => void }) {
  const tone = toast.tone ?? 'info'
  const Icon = TONE_ICON[tone]
  const { holders } = useExpiry(TOAST_MS[tone], () => onDismiss(toast.id))
  return (
    <div className={cx(styles.toast, styles[tone])} {...holders} role={tone === 'critical' ? 'alert' : undefined}>
      <span className={styles.toneIcon}>
        <Icon size={18} aria-hidden="true" />
      </span>
      <div className={styles.text}>
        <span className={styles.title}>{toast.title}</span>
        {toast.body && <span className={styles.body}>{toast.body}</span>}
      </div>
      {toast.action && (
        <button
          type="button"
          className={styles.action}
          onClick={() => {
            toast.action?.run()
            onDismiss(toast.id)
          }}
        >
          {toast.action.label}
        </button>
      )}
      <Dismiss onClick={() => onDismiss(toast.id)} />
    </div>
  )
}

function Dismiss({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className={styles.dismiss} aria-label="Dismiss" onClick={onClick}>
      <X size={15} aria-hidden="true" />
    </button>
  )
}
