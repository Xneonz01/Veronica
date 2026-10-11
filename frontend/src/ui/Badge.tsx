// A badge carries state with a word and a shape, never with colour alone: it
// always has a label.

import type { ReactNode } from 'react'
import styles from './Badge.module.css'
import { cx } from './cx'

export type BadgeTone = 'neutral' | 'success' | 'assistant' | 'info' | 'warning' | 'danger'

export type BadgeProps = {
  tone?: BadgeTone
  icon?: ReactNode
  children: ReactNode
  /** Filled reads louder: one per view at most. */
  solid?: boolean
  size?: 'sm' | 'md'
  className?: string
}

export function Badge({
  tone = 'neutral',
  icon,
  children,
  solid = false,
  size = 'md',
  className,
}: BadgeProps) {
  return (
    <span className={cx(styles.badge, styles[size], styles[tone], solid && styles.solid, className)}>
      {icon}
      {children}
    </span>
  )
}
