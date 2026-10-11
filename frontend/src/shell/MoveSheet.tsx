// Filing a conversation: the projects as a short list, the current one marked.
// Choosing one moves it at once; it's as easy to move back.

import { Check, Folder, FolderMinus } from 'lucide-react'
import { useState } from 'react'
import type { ConversationSummary } from '../chat/types'
import { Notice } from '../ui/Feedback'
import { Sheet } from '../ui/Sheet'
import { useWorkspace } from '../workspace/context'
import { titleOf } from '../workspace/recents'
import styles from './Recents.module.css'
import rows from './SidebarRow.module.css'

export function MoveSheet({
  conversation,
  onClose,
}: {
  conversation: ConversationSummary | null
  onClose: () => void
}) {
  const { projects, moveConversation } = useWorkspace()
  const [failed, setFailed] = useState(false)
  // One move at a time: two quick picks would send two moves that can land in
  // either order, filing it in the one you didn't mean last.
  const [moving, setMoving] = useState(false)

  async function move(projectId: number | null) {
    if (!conversation || moving) return
    setMoving(true)
    const moved = await moveConversation(conversation.id, projectId)
    setMoving(false)
    setFailed(!moved)
    if (moved) onClose()
  }

  return (
    <Sheet
      open={conversation !== null}
      onClose={() => {
        setFailed(false)
        onClose()
      }}
      title="Move to project"
      description={conversation ? `“${titleOf(conversation)}”` : undefined}
      size="sm"
    >
      {projects.length === 0 ? (
        <p className={styles.sheetEmpty}>
          There are no projects yet. Make one from the sidebar, then file conversations in it.
        </p>
      ) : (
        <ul className={styles.choices}>
          {projects.map((project) => {
            const current = conversation?.project_id === project.id
            return (
              <li key={project.id}>
                <button
                  type="button"
                  className={rows.row}
                  aria-current={current ? 'true' : undefined}
                  disabled={moving}
                  onClick={() => void move(project.id)}
                >
                  <Folder size={17} aria-hidden="true" className={rows.icon} />
                  <span className={rows.label}>{project.name}</span>
                  {current && <Check size={16} aria-label="Filed here" />}
                </button>
              </li>
            )
          })}
          {conversation?.project_id != null && (
            <li>
              <button type="button" className={rows.row} disabled={moving} onClick={() => void move(null)}>
                <FolderMinus size={17} aria-hidden="true" className={rows.icon} />
                <span className={rows.label}>Take it out of its project</span>
              </button>
            </li>
          )}
        </ul>
      )}
      {failed && (
        <Notice tone="critical" live className={styles.sheetNotice}>
          Couldn't move it just now. Try again in a moment.
        </Notice>
      )}
    </Sheet>
  )
}
