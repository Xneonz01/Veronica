// The exchange as it happens: what you are saying, softer until she has it, and
// what she is saying back, with a caret while she speaks. What her reply said she did
// and she didn't sits under it, quiet, as chat shows it under an answer.

import { CircleAlert } from 'lucide-react'
import type { CallState } from '../audio/useRealtimeCall'
import { cx } from '../ui/cx'
import styles from './LiveLines.module.css'

export function LiveLines({
  finalText,
  partial,
  reply,
  caveat = '',
  state,
}: {
  finalText: string
  partial: string
  reply: string
  caveat?: string
  state: CallState
}) {
  const yours = finalText || partial
  const hearing = !finalText && partial.length > 0
  const answering = state === 'thinking' || state === 'speaking'
  return (
    <dl className={styles.lines}>
      <div className={styles.line}>
        <dt className={styles.who}>You</dt>
        <dd className={cx(styles.said, hearing && styles.hearing)}>
          {yours || <span className={styles.waiting}>{state === 'listening' ? 'Listening…' : '—'}</span>}
        </dd>
      </div>
      <div className={styles.line}>
        <dt className={cx(styles.who, styles.her)}>Veronica</dt>
        <dd className={styles.said}>
          {reply || <span className={styles.waiting}>{state === 'thinking' ? 'Thinking…' : '—'}</span>}
          {answering && reply && <span className={styles.caret} aria-hidden="true" />}
          {caveat && (
            <span className={styles.caveat}>
              <CircleAlert size={16} className={styles.caveatIcon} aria-hidden="true" />
              {caveat}
            </span>
          )}
        </dd>
      </div>
    </dl>
  )
}
