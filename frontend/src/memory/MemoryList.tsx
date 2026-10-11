// What she remembers, one line each: the memory, then what kind it is and where
// it came from. A row edits in place; its menu pins it, edits it, or forgets it
// after asking. What she keeps about you ("About you") can be edited, not
// forgotten or pinned: it is always part of what she knows.

import { Ellipsis, Pencil, Pin, PinOff, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button, IconButton } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { cx } from '../ui/cx'
import { TextArea } from '../ui/Field'
import { Menu } from '../ui/Menu'
import { Popover } from '../ui/Popover'
import { certainty, kindLabel, origin, slotLabel } from './labels'
import styles from './MemoryList.module.css'
import type { MemoryRow } from './types'

export type MemoryActions = {
  onPin: (id: number, pinned: boolean) => void
  onEdit: (id: number, content: string) => void
  onForget: (id: number) => void
}

export function MemoryList({ memories, ...actions }: { memories: readonly MemoryRow[] } & MemoryActions) {
  const anchor = useRef<HTMLButtonElement | null>(null)
  const [menuFor, setMenuFor] = useState<MemoryRow | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [forgetting, setForgetting] = useState<MemoryRow | null>(null)
  // An edit that ends takes its field away: the keyboard goes back to the row's
  // options button, where the edit began, not to the page body.
  const refocus = useRef<number | null>(null)

  return (
    <>
      <ul className={styles.list}>
        {memories.map((memory) => {
          const profile = memory.kind === 'profile'
          return (
            <li key={memory.id} className={cx(styles.row, memory.pinned && styles.pinned)}>
              {editing === memory.id ? (
                <EditRow
                  memory={memory}
                  onDone={(content) => {
                    setEditing(null)
                    refocus.current = memory.id
                    if (content !== null && content !== memory.content) actions.onEdit(memory.id, content)
                  }}
                />
              ) : (
                <>
                  <div className={styles.text}>
                    <p className={cx(styles.content, memory.source === 'inferred' && styles.inferred)}>
                      {profile && <span className={styles.slot}>{slotLabel(memory.key)}: </span>}
                      {memory.content}
                    </p>
                    <p className={styles.meta}>
                      <span>{kindLabel(memory.kind)}</span>
                      <span aria-hidden="true">·</span>
                      <span>{origin(memory)}</span>
                      {memory.source === 'inferred' && (
                        <>
                          <span aria-hidden="true">·</span>
                          <span>{certainty(memory.confidence)}</span>
                        </>
                      )}
                      {memory.pinned && (
                        <span className={styles.pinBadge}>
                          <Pin size={12} aria-hidden="true" />
                          Pinned
                        </span>
                      )}
                    </p>
                  </div>
                  <IconButton
                    ref={(element) => {
                      if (element && refocus.current === memory.id) {
                        refocus.current = null
                        element.focus()
                      }
                    }}
                    size="sm"
                    className={styles.more}
                    label="Options for this memory"
                    icon={<Ellipsis size={16} aria-hidden="true" />}
                    aria-haspopup="menu"
                    aria-expanded={menuFor?.id === memory.id}
                    onClick={(event) => {
                      anchor.current = event.currentTarget
                      setMenuFor((open) => (open?.id === memory.id ? null : memory))
                    }}
                  />
                </>
              )}
            </li>
          )
        })}
      </ul>

      <Popover anchor={anchor} open={menuFor !== null} onClose={() => setMenuFor(null)} placement="bottom-end" label="Memory options">
        {menuFor && (
          <Menu
            label="Memory options"
            onDone={() => setMenuFor(null)}
            items={[
              ...(menuFor.kind === 'profile'
                ? []
                : [
                    {
                      id: 'pin',
                      label: menuFor.pinned ? 'Unpin' : 'Pin',
                      hint: menuFor.pinned ? undefined : 'Kept for good, first to come to mind',
                      icon: menuFor.pinned ? <PinOff size={16} /> : <Pin size={16} />,
                      onSelect: () => actions.onPin(menuFor.id, !menuFor.pinned),
                    },
                  ]),
              { id: 'edit', label: 'Edit', icon: <Pencil size={16} />, onSelect: () => setEditing(menuFor.id) },
              ...(menuFor.kind === 'profile'
                ? []
                : [{ id: 'forget', label: 'Forget', icon: <Trash2 size={16} />, destructive: true, onSelect: () => setForgetting(menuFor) }]),
            ]}
          />
        )}
      </Popover>

      <Confirm
        open={forgetting !== null}
        title="Forget this?"
        confirmLabel="Forget"
        cancelLabel="Keep it"
        destructive
        onConfirm={() => {
          if (forgetting) actions.onForget(forgetting.id)
          setForgetting(null)
        }}
        onCancel={() => setForgetting(null)}
      >
        “{forgetting?.content}” She won't bring it up again.
      </Confirm>
    </>
  )
}

function EditRow({ memory, onDone }: { memory: MemoryRow; onDone: (content: string | null) => void }) {
  const [draft, setDraft] = useState(memory.content)
  return (
    <div className={styles.edit}>
      <TextArea
        label="Edit this memory"
        hideLabel
        value={draft}
        rows={2}
        autoFocus
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          // Handled here, so the key doesn't go on to press the button focus returns to.
          if (event.key === 'Escape') {
            event.preventDefault()
            onDone(null)
          }
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault()
            onDone(draft.trim() || null)
          }
        }}
      />
      <div className={styles.editActions}>
        <Button size="sm" variant="ghost" onClick={() => onDone(null)}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!draft.trim()} onClick={() => onDone(draft.trim())}>
          Save
        </Button>
      </div>
    </div>
  )
}
