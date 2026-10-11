// The command palette (Ctrl K): go anywhere, act, find a conversation or a
// project, or ask her straight away. A combobox over a grouped list: typing
// narrows, the arrow keys move the highlight, Enter runs it, Escape closes.

import { Search } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { cx } from '../ui/cx'
import { Kbd } from '../ui/Kbd'
import styles from './CommandPalette.module.css'
import { GROUPS, narrow, type PaletteItem } from './palette'

export function CommandPalette({
  open,
  onClose,
  items,
}: {
  open: boolean
  onClose: () => void
  /** Everything it can offer for this query; the Ask item is built by the caller. */
  items: (query: string) => PaletteItem[]
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const results = useMemo(() => narrow(items(query), query), [items, query])

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) {
      setQuery('')
      setActive(0)
      element.showModal()
      input.current?.focus()
    }
    if (!open && element.open) element.close()
  }, [open])

  // The highlighted row stays in view as the arrow keys move it.
  useEffect(() => {
    document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: 'nearest' })
  }, [active, listId])

  function run(item: PaletteItem | undefined) {
    if (!item) return
    onClose()
    item.run()
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((current) => (current + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((current) => (current - 1 + results.length) % results.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      run(results[active])
    }
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-label="Command palette"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClose={() => {
        if (open) onClose()
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className={styles.field}>
        <Search size={18} className={styles.searchIcon} aria-hidden="true" />
        <input
          ref={input}
          className={styles.input}
          role="combobox"
          aria-label="Search or ask"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={results.length ? `${listId}-${active}` : undefined}
          placeholder="Search, go to, or ask Veronica"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          spellCheck={false}
          autoComplete="off"
        />
        <Kbd>Esc</Kbd>
      </div>
      <div id={listId} role="listbox" aria-label="Results" className={styles.list}>
        {results.length === 0 ? (
          <p className={styles.empty}>Nothing matches “{query}”.</p>
        ) : (
          GROUPS.map((group) => {
            const members = results.filter((item) => item.group === group)
            if (members.length === 0) return null
            return (
              <div key={group} role="group" aria-labelledby={`${listId}-group-${GROUPS.indexOf(group)}`} className={styles.group}>
                <div id={`${listId}-group-${GROUPS.indexOf(group)}`} className={styles.groupLabel}>
                  {group}
                </div>
                {members.map((item) => {
                  const index = results.indexOf(item)
                  return (
                    <div
                      key={item.id}
                      id={`${listId}-${index}`}
                      role="option"
                      aria-selected={index === active}
                      className={cx(styles.option, index === active && styles.active)}
                      onPointerMove={() => setActive(index)}
                      onClick={() => run(item)}
                    >
                      <span className={styles.icon}>{item.icon}</span>
                      <span className={styles.label}>{item.label}</span>
                      {item.hint && <span className={styles.hint}>{item.hint}</span>}
                    </div>
                  )
                })}
              </div>
            )
          })
        )}
      </div>
      <footer className={styles.footer} aria-hidden="true">
        <span>
          <Kbd>↑</Kbd> <Kbd>↓</Kbd> to move
        </span>
        <span>
          <Kbd>Enter</Kbd> to open
        </span>
      </footer>
    </dialog>
  )
}
