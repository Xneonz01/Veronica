// A receipt under the answer when she wrote to her memory during it: what she
// kept, in one line, with Undo while it can still be undone. The same write also
// shows as a toast; this one stays with the conversation it happened in.

import { Bookmark } from 'lucide-react'
import type { MemoryAction, Notification } from '../notify/types'
import styles from './MemoryLine.module.css'

// A superseded memory is one she was told was wrong: the old one is kept for Undo.
const LABEL: Partial<Record<MemoryAction, string>> = { removed: 'Forgot', superseded: 'Corrected' }

export function MemoryLine({ receipt, onUndo }: { receipt: Notification; onUndo: () => void }) {
  return (
    <p className={styles.line}>
      <Bookmark size={15} className={styles.icon} aria-hidden="true" />
      <span className={styles.label}>{LABEL[receipt.action] ?? 'Remembered'}</span>
      <span className={styles.summary}>{receipt.summary}</span>
      {receipt.undoable && (
        <button type="button" className={styles.undo} onClick={onUndo}>
          Undo
        </button>
      )}
    </p>
  )
}
