// The two shapes every setting takes: a titled group on one card, and inside it
// rows of a label and what it changes on the left, the control on the right. A
// Switch is already a row, so it sits in a group as it is.

import { useId, type ReactNode } from 'react'
import styles from './Group.module.css'

export function Group({
  title,
  lead,
  bare = false,
  children,
}: {
  title: string
  lead?: ReactNode
  /** Without the card: for a control that is made of cards itself. */
  bare?: boolean
  children: ReactNode
}) {
  const heading = useId()
  return (
    <section className={styles.group} aria-labelledby={heading}>
      <div className={styles.head}>
        <h2 id={heading} className={styles.title}>
          {title}
        </h2>
        {lead != null && <p className={styles.lead}>{lead}</p>}
      </div>
      {bare ? children : <div className={styles.card}>{children}</div>}
    </section>
  )
}

/** A setting whose control is not a switch. The label here is what you read; the
    control still carries its own accessible name. */
export function Row({
  label,
  description,
  children,
  below,
}: {
  label: ReactNode
  description?: ReactNode
  children: ReactNode
  /** Shown under the whole row: a problem with this setting, in words. */
  below?: ReactNode
}) {
  return (
    <div className={styles.row}>
      <div className={styles.line}>
        <div className={styles.text}>
          <span className={styles.label}>{label}</span>
          {description != null && <span className={styles.description}>{description}</span>}
        </div>
        <div className={styles.control}>{children}</div>
      </div>
      {below}
    </div>
  )
}
