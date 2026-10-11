// The states every async surface needs: a skeleton shaped like what it replaces,
// work in progress named in words, an empty state that says what to do, and a
// notice for everything else. None of them is decorative.

import { CircleAlert, CircleCheck, Info, TriangleAlert } from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'
import { cx } from './cx'
import styles from './Feedback.module.css'

export type NoticeTone = 'info' | 'attention' | 'critical' | 'positive'

const NOTICE_ICON = {
  info: Info,
  attention: TriangleAlert,
  critical: CircleAlert,
  positive: CircleCheck,
} as const

export type NoticeProps = {
  tone?: NoticeTone
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  /** Announced once: politely (role="status"), or at once for a critical notice
      (role="alert"). A status region that arrives already holding its words is
      often skipped by screen readers; an alert is read the moment it appears. */
  live?: boolean
  className?: string
}

/** One icon per tone and a sentence: a colour never carries the meaning alone. */
export function Notice({ tone = 'info', title, children, action, live = false, className }: NoticeProps) {
  const Icon = NOTICE_ICON[tone]
  return (
    <div className={cx(styles.notice, styles[tone], className)} role={live ? (tone === 'critical' ? 'alert' : 'status') : undefined}>
      <span className={styles.noticeIcon}>
        <Icon size={16} aria-hidden="true" />
      </span>
      <div className={styles.noticeText}>
        {title != null && <span className={styles.noticeTitle}>{title}</span>}
        {children != null && <span className={styles.noticeBody}>{children}</span>}
      </div>
      {action != null && <div className={styles.noticeAction}>{action}</div>}
    </div>
  )
}

export type EmptyStateProps = {
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
  icon?: ReactNode
  className?: string
}

/** Start-aligned and text-first: calm copy and, when there is one, the next step. */
export function EmptyState({ title, children, action, icon, className }: EmptyStateProps) {
  return (
    <div className={cx(styles.empty, className)}>
      {icon != null && <span className={styles.emptyIcon}>{icon}</span>}
      <p className={styles.emptyTitle}>{title}</p>
      {children != null && <p className={styles.emptyBody}>{children}</p>}
      {action != null && <div className={styles.emptyAction}>{action}</div>}
    </div>
  )
}

/** Work in progress, named: an activity sentence and three quiet dots. */
export function Working({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx(styles.working, className)} role="status">
      <span className={styles.dots} aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      {children}
    </div>
  )
}

export type SkeletonProps = {
  width?: CSSProperties['width']
  height?: CSSProperties['height']
  radius?: number
  className?: string
}

/** A placeholder shaped like the content it becomes. */
export function Skeleton({ width = '100%', height = 16, radius, className }: SkeletonProps) {
  return (
    <span
      className={cx(styles.skeleton, className)}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  )
}
