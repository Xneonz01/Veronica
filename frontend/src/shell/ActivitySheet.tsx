// What she wrote to her memory since the window opened, so a toast missed during a
// call isn't lost. Each entry offers Undo while it still can be undone.

import { History } from 'lucide-react'
import { relativeTime } from '../memory/format'
import type { MemoryAction } from '../notify/types'
import { useNotifications } from '../notify/useNotifications'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/Feedback'
import { Sheet } from '../ui/Sheet'
import styles from './ActivitySheet.module.css'

const ACTION: Record<MemoryAction, { label: string; tone: BadgeTone }> = {
  added: { label: 'Remembered', tone: 'assistant' },
  superseded: { label: 'Updated', tone: 'assistant' },
  updated: { label: 'Edited', tone: 'neutral' },
  candidate: { label: 'Suggested', tone: 'info' },
  removed: { label: 'Forgotten', tone: 'neutral' },
}

export function ActivitySheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { history, undo } = useNotifications()
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Memory activity"
      description="What Veronica remembered, changed or forgot since the app opened."
    >
      {history.length === 0 ? (
        <EmptyState icon={<History size={22} aria-hidden="true" />} title="Nothing yet">
          When she remembers or forgets something, it's listed here, with Undo while it still applies.
        </EmptyState>
      ) : (
        <ul className={styles.list}>
          {history.map((entry) => {
            const action = ACTION[entry.action]
            return (
              <li key={entry.id} className={styles.entry}>
                <div className={styles.text}>
                  <span className={styles.summary}>{entry.summary}</span>
                  <span className={styles.meta}>
                    <Badge tone={action.tone} size="sm">
                      {action.label}
                    </Badge>
                    <span>{relativeTime(entry.ts ?? null) ?? 'just now'}</span>
                  </span>
                </div>
                {entry.undoable && (
                  <Button size="sm" variant="ghost" onClick={() => undo(entry)}>
                    Undo
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Sheet>
  )
}
