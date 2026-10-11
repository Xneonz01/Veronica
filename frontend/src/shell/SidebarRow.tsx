// One row of the sidebar: a destination, an action, a project or a conversation.
// In the rail the label is kept for screen readers and shown as the hint on hover,
// so every row is named in both layouts.

import { useContext, type ComponentPropsWithRef, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { cx } from '../ui/cx'
import { RailContext } from './rail'
import styles from './SidebarRow.module.css'

type RowContent = {
  icon: ReactNode
  label: string
  /** A count or a key hint, right-aligned; dropped in the rail. */
  meta?: ReactNode
  /** A dot for something unseen, on the icon so it survives the rail. */
  dot?: boolean
}

function Content({ icon, label, meta, dot }: RowContent) {
  const rail = useContext(RailContext)
  return (
    <>
      <span className={styles.icon}>
        {icon}
        {dot && <span className={styles.dot} aria-hidden="true" />}
      </span>
      <span className={rail ? 'visually-hidden' : styles.label}>{label}</span>
      {!rail && meta != null && <span className={styles.meta}>{meta}</span>}
    </>
  )
}

export function RowLink({ to, ...content }: RowContent & { to: string }) {
  const rail = useContext(RailContext)
  return (
    <NavLink to={to} className={cx(styles.row, rail && styles.rail)} title={rail ? content.label : undefined}>
      <Content {...content} />
    </NavLink>
  )
}

export type RowButtonProps = RowContent &
  Omit<ComponentPropsWithRef<'button'>, 'children'> & { current?: boolean }

export function RowButton({ icon, label, meta, dot, current, className, ...rest }: RowButtonProps) {
  const rail = useContext(RailContext)
  return (
    <button
      type="button"
      {...rest}
      className={cx(styles.row, rail && styles.rail, className)}
      aria-current={current ? 'true' : undefined}
      title={rail ? label : rest.title}
    >
      <Content icon={icon} label={label} meta={meta} dot={dot} />
    </button>
  )
}
