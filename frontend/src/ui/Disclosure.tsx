// Something that opens in place: a native <details>, so the keyboard and a screen
// reader know it without extra ARIA, with a chevron that turns as it opens, the
// one sign that it can.

import type { ReactNode } from 'react'
import { cx } from './cx'
import styles from './Disclosure.module.css'

export function Disclosure({
  summary,
  children,
  open = false,
  className,
}: {
  summary: ReactNode
  children: ReactNode
  /** Open when it first appears; after that it is yours to open and close. */
  open?: boolean
  className?: string
}) {
  return (
    <details className={cx(styles.disclosure, className)} open={open}>
      <summary className={styles.summary}>{summary}</summary>
      {children}
    </details>
  )
}
