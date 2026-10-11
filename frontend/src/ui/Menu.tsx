// A list of actions, usually inside a Popover. It opens with the first item
// focused; the arrow keys move and wrap, Home and End jump, a letter jumps to
// the next item that starts with it, and Tab leaves. Choosing an item runs it
// and then calls onDone, which is where the owner closes the popover.

import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from './cx'
import styles from './Menu.module.css'

export type MenuItem = {
  id: string
  label: string
  hint?: string
  icon?: ReactNode
  /** Irreversible or removing: drawn in red, and it still confirms after. */
  destructive?: boolean
  onSelect: () => void
}

export type MenuProps = {
  items: readonly MenuItem[]
  label: string
  onDone?: () => void
}

export function Menu({ items, label, onDone }: MenuProps) {
  const list = useRef<HTMLDivElement>(null)

  useEffect(() => {
    menuItems(list.current)[0]?.focus()
  }, [])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const buttons = menuItems(list.current)
    if (buttons.length === 0) return
    const current = buttons.findIndex((button) => button === document.activeElement)
    const last = buttons.length - 1
    let next: number | null = null

    if (event.key === 'ArrowDown') next = current >= last ? 0 : current + 1
    else if (event.key === 'ArrowUp') next = current <= 0 ? last : current - 1
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = last
    else if (event.key === 'Tab') {
      // Tab leaves a menu rather than walking it; the popover hands focus back
      // to the control that opened it.
      event.preventDefault()
      onDone?.()
      return
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      next = startingWith(buttons, event.key, current)
    }

    if (next === null) return
    event.preventDefault()
    buttons[next]?.focus()
  }

  return (
    <div ref={list} role="menu" aria-label={label} className={styles.menu} onKeyDown={onKeyDown}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          tabIndex={-1}
          className={cx(styles.item, item.destructive && styles.danger)}
          onPointerMove={(event) => event.currentTarget.focus()}
          onClick={() => {
            item.onSelect()
            onDone?.()
          }}
        >
          {item.icon != null && (
            <span className={styles.icon} aria-hidden="true">
              {item.icon}
            </span>
          )}
          <span className={styles.text}>
            <span className={styles.label}>{item.label}</span>
            {item.hint != null && <span className={styles.hint}>{item.hint}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}

function menuItems(list: HTMLElement | null): HTMLElement[] {
  return Array.from(list?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
}

/** The next item after `from` whose label starts with `letter`, wrapping; null if none. */
function startingWith(buttons: HTMLElement[], letter: string, from: number): number | null {
  const wanted = letter.toLocaleLowerCase()
  for (let step = 1; step <= buttons.length; step += 1) {
    const index = (from + step) % buttons.length
    if (buttons[index].textContent?.trim().toLocaleLowerCase().startsWith(wanted)) return index
  }
  return null
}
