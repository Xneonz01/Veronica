// A ring that empties over a duration: the time left to undo, or to answer a
// permission request. It is a visual aid only; whatever expires is timed
// elsewhere, and the same fact is always said in words beside it.

import type { CSSProperties } from 'react'
import styles from './Countdown.module.css'

export function Countdown({
  durationMs,
  elapsedMs = 0,
  paused = false,
  size = 28,
}: {
  durationMs: number
  /** Time already gone, for a ring that starts part-way (a prompt that was resynced). */
  elapsedMs?: number
  paused?: boolean
  size?: number
}) {
  const radius = size / 2 - 2
  const arc: CSSProperties = {
    animationDuration: `${durationMs}ms`,
    animationDelay: `${-elapsedMs}ms`,
    animationPlayState: paused ? 'paused' : 'running',
  }
  return (
    <svg className={styles.ring} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle className={styles.track} cx={size / 2} cy={size / 2} r={radius} pathLength={100} />
      <circle className={styles.arc} cx={size / 2} cy={size / 2} r={radius} pathLength={100} style={arc} />
    </svg>
  )
}
