// What she picked up and wants your word on before keeping: each with how sure
// she is, and Keep or Drop. Nothing here is used until you keep it.

import { Button } from '../ui/Button'
import { certainty, origin } from './labels'
import styles from './MemoryList.module.css'
import review from './ReviewList.module.css'
import type { MemoryRow } from './types'

export function ReviewList({
  candidates,
  onKeep,
  onDrop,
}: {
  candidates: readonly MemoryRow[]
  onKeep: (id: number) => void
  onDrop: (id: number) => void
}) {
  return (
    <ul className={styles.list}>
      {candidates.map((candidate) => (
        <li key={candidate.id} className={styles.row}>
          <div className={styles.text}>
            <p className={styles.content}>{candidate.content}</p>
            <p className={styles.meta}>
              <span>{origin(candidate)}</span>
              <span aria-hidden="true">·</span>
              <span>{certainty(candidate.confidence)}</span>
            </p>
          </div>
          <span className={review.actions}>
            <Button size="sm" variant="ghost" onClick={() => onDrop(candidate.id)}>
              Drop
            </Button>
            <Button size="sm" variant="primary" onClick={() => onKeep(candidate.id)}>
              Keep
            </Button>
          </span>
        </li>
      ))}
    </ul>
  )
}
