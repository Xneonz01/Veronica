// The conversation list, grouped by day. Each row opens its conversation; its menu
// renames it in place, files it in a project, or deletes it after asking.

import { Ellipsis, FolderInput, Pencil, Trash2, X } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { ConversationSummary } from '../chat/types'
import { IconButton } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { Skeleton } from '../ui/Feedback'
import { Menu } from '../ui/Menu'
import { Popover } from '../ui/Popover'
import { useWorkspace } from '../workspace/context'
import { groupByAge, inProject, titleOf } from '../workspace/recents'
import { ListProblem } from './ListProblem'
import { MoveSheet } from './MoveSheet'
import styles from './Recents.module.css'
import { RenameField } from './RenameField'
import rows from './SidebarRow.module.css'
import { useToasts } from './toast'

export function Recents() {
  const workspace = useWorkspace()
  const toasts = useToasts()
  const { conversations, projects, activeProjectId, openConversationId } = workspace
  const project = projects.find((item) => item.id === activeProjectId) ?? null
  const groups = useMemo(
    () => groupByAge(inProject(conversations, activeProjectId), new Date()),
    [conversations, activeProjectId],
  )

  const anchor = useRef<HTMLButtonElement | null>(null)
  const [menuFor, setMenuFor] = useState<ConversationSummary | null>(null)
  const [renaming, setRenaming] = useState<number | null>(null)
  const refocus = useRef<number | null>(null)
  const [moving, setMoving] = useState<ConversationSummary | null>(null)
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function rename(id: number, title: string) {
    if (await workspace.renameConversation(id, title)) return
    toasts.show({ tone: 'critical', title: "Couldn't rename it", body: 'It keeps its old name. Try again in a moment.' })
  }

  async function confirmDelete() {
    if (!deleting) return
    setBusy(true)
    const removed = await workspace.removeConversation(deleting.id)
    setBusy(false)
    setFailed(!removed)
    if (removed) setDeleting(null)
  }

  return (
    <section className={styles.recents} aria-labelledby="recents-title">
      <header className={styles.head}>
        <h2 id="recents-title" className={styles.title}>
          {project ? project.name : 'Recents'}
        </h2>
        {project && (
          <IconButton
            size="sm"
            label="Show all conversations"
            icon={<X size={15} aria-hidden="true" />}
            onClick={() => workspace.selectProject(null)}
          />
        )}
      </header>

      {workspace.conversationsLoading ? (
        <div className={styles.loading} aria-hidden="true">
          {[70, 52, 64, 46].map((width) => (
            <Skeleton key={width} width={`${width}%`} height={12} />
          ))}
        </div>
      ) : workspace.conversationsError && conversations.length === 0 ? (
        <ListProblem message={workspace.conversationsError} onRetry={workspace.reloadLists} />
      ) : groups.length === 0 ? (
        <p className={styles.empty}>
          {project ? 'Nothing in this project yet. A new chat here is filed in it.' : 'Your conversations will be listed here.'}
        </p>
      ) : (
        groups.map((group) => (
          <div key={group.label} className={styles.group}>
            <h3 className={styles.groupLabel}>{group.label}</h3>
            <ul className={styles.list}>
              {group.items.map((item) => (
                <li key={item.id} className={styles.item}>
                  {renaming === item.id ? (
                    <RenameField
                      label="Conversation name"
                      initial={item.title}
                      onDone={(title, how) => {
                        setRenaming(null)
                        if (how !== 'blur') refocus.current = item.id
                        if (title && title !== item.title) void rename(item.id, title)
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className={rows.row}
                        aria-current={item.id === openConversationId ? 'true' : undefined}
                        onClick={() => workspace.openConversation(item.id)}
                      >
                        <span className={rows.label}>{titleOf(item)}</span>
                      </button>
                      <IconButton
                        ref={(element) => {
                          // A rename ended from the keyboard hands it back to this button.
                          if (element && refocus.current === item.id) {
                            refocus.current = null
                            element.focus()
                          }
                        }}
                        size="sm"
                        className={styles.more}
                        label={`Options for “${titleOf(item)}”`}
                        icon={<Ellipsis size={16} aria-hidden="true" />}
                        aria-haspopup="menu"
                        aria-expanded={menuFor?.id === item.id}
                        onClick={(event) => {
                          anchor.current = event.currentTarget
                          setMenuFor((open) => (open?.id === item.id ? null : item))
                        }}
                      />
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      <Popover
        anchor={anchor}
        open={menuFor !== null}
        onClose={() => setMenuFor(null)}
        placement="bottom-end"
        label="Conversation options"
      >
        {menuFor && (
          <Menu
            label="Conversation options"
            onDone={() => setMenuFor(null)}
            items={[
              { id: 'rename', label: 'Rename', icon: <Pencil size={16} />, onSelect: () => setRenaming(menuFor.id) },
              {
                id: 'move',
                label: 'Move to project',
                icon: <FolderInput size={16} />,
                onSelect: () => setMoving(menuFor),
              },
              {
                id: 'delete',
                label: 'Delete',
                icon: <Trash2 size={16} />,
                destructive: true,
                onSelect: () => {
                  setFailed(false)
                  setDeleting(menuFor)
                },
              },
            ]}
          />
        )}
      </Popover>

      <MoveSheet conversation={moving} onClose={() => setMoving(null)} />

      <Confirm
        open={deleting !== null}
        title="Delete this conversation?"
        confirmLabel="Delete"
        cancelLabel="Keep it"
        destructive
        busy={busy}
        error={failed ? "Couldn't delete it just now. Try again in a moment." : null}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      >
        “{deleting && titleOf(deleting)}” is deleted, with its messages and the files attached to it. What
        Veronica learned from it stays in her memory.
      </Confirm>
    </section>
  )
}
