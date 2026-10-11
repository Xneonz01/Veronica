// Tabs over one page's panels: a tablist with a roving tab stop, arrows and Home
// or End to move (a tab opens as it is reached), and an optional count on each.
// The page renders the panel itself with panelProps() from tabPanel.ts.

import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from './cx'
import styles from './Tabs.module.css'

export type Tab<T extends string> = { value: T; label: ReactNode; count?: number }

export function Tabs<T extends string>({
  id,
  label,
  value,
  tabs,
  onChange,
}: {
  /** Prefix for the tab and panel ids, unique on the page. */
  id: string
  label: string
  value: T
  tabs: readonly Tab<T>[]
  onChange: (value: T) => void
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const current = Math.max(0, tabs.findIndex((tab) => tab.value === value))

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const last = tabs.length - 1
    const moves: Record<string, number> = {
      ArrowRight: current === last ? 0 : current + 1,
      ArrowLeft: current === 0 ? last : current - 1,
      Home: 0,
      End: last,
    }
    if (!(event.key in moves)) return
    event.preventDefault()
    const next = moves[event.key]
    onChange(tabs[next].value)
    buttons.current[next]?.focus()
  }

  return (
    <div role="tablist" aria-label={label} className={styles.tabs} onKeyDown={onKeyDown}>
      {tabs.map((tab, index) => {
        const selected = index === current
        return (
          <button
            key={tab.value}
            ref={(element) => {
              buttons.current[index] = element
            }}
            type="button"
            role="tab"
            id={`${id}-tab-${tab.value}`}
            aria-selected={selected}
            aria-controls={`${id}-panel-${tab.value}`}
            tabIndex={selected ? 0 : -1}
            className={cx(styles.tab, selected && styles.selected)}
            onClick={() => onChange(tab.value)}
          >
            {tab.label}
            {tab.count !== undefined && <span className={cx(styles.count, 'num')}>{tab.count}</span>}
          </button>
        )
      })}
    </div>
  )
}

