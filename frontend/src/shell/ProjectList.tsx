// Projects: folders that scope what she can cite. Choosing one lists its
// conversations and starts a new chat inside it; choosing it again shows all.

import { Ellipsis, Folder, FolderPlus, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import type { Project } from '../chat/useProjects'
import { IconButton } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { Menu } from '../ui/Menu'
import { Popover } from '../ui/Popover'
import { useWorkspace } from '../workspace/context'
import { ListProblem } from './ListProblem'
import styles from './Recents.module.css'
import { RenameField } from './RenameField'
import rows from './SidebarRow.module.css'
import { useToasts } from './toast'

export function ProjectList() {
  const { projects, projectsLoading, projectsError, reloadLists, activeProjectId, selectProject, createProject, removeProject } = useWorkspace()
  const [creating, setCreating] = useState(false)
  const newProject = useRef<HTMLButtonElement>(null)
  const anchor = useRef<HTMLButtonElement | null>(null)
  const [menuFor, setMenuFor] = useState<Project | null>(null)
  const [deleting, setDeleting] = useState<Project | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const toasts = useToasts()

  async function confirmDelete() {
    if (!deleting) return
    setBusy(true)
    const removed = await removeProject(deleting.id)
    setBusy(false)
    setFailed(!removed)
    if (removed) setDeleting(null)
  }

  async function create(name: string, open: boolean) {
    if (await createProject(name, open)) return
    toasts.show({ tone: 'critical', title: `Couldn't create “${name}”`, body: 'Try again in a moment.' })
  }

  return (
    <section className={styles.recents} aria-labelledby="projects-title">
      <header className={styles.head}>
        <h2 id="projects-title" className={styles.title}>
          Projects
        </h2>
        <IconButton
          ref={newProject}
          size="sm"
          label="New project"
          icon={<FolderPlus size={16} aria-hidden="true" />}
          onClick={() => setCreating(true)}
        />
      </header>
      <ul className={styles.list}>
        {creating && (
          <li className={styles.item}>
            <RenameField
              label="New project name"
              initial=""
              placeholder="Name the project"
              onDone={(name, how) => {
                setCreating(false)
                if (name) void create(name, how === 'enter')
                // Ended from the keyboard, the field it was in has gone: back to the
                // button that opened it. A click elsewhere already put focus there.
                if (how !== 'blur' && !(name && how === 'enter')) newProject.current?.focus()
              }}
            />
          </li>
        )}
        {projects.map((project) => {
          const active = project.id === activeProjectId
          return (
            <li key={project.id} className={styles.item}>
              <button
                type="button"
                className={rows.row}
                aria-current={active ? 'true' : undefined}
                onClick={() => selectProject(active ? null : project.id)}
              >
                <span className={rows.icon}>
                  <Folder size={17} aria-hidden="true" />
                </span>
                <span className={rows.label}>{project.name}</span>
                <span
                  className={rows.meta}
                  aria-label={`${project.conversation_count} ${project.conversation_count === 1 ? 'conversation' : 'conversations'}`}
                >
                  {project.conversation_count}
                </span>
              </button>
              <IconButton
                size="sm"
                className={styles.more}
                label={`Options for ${project.name}`}
                icon={<Ellipsis size={16} aria-hidden="true" />}
                aria-haspopup="menu"
                aria-expanded={menuFor?.id === project.id}
                onClick={(event) => {
                  anchor.current = event.currentTarget
                  setMenuFor((open) => (open?.id === project.id ? null : project))
                }}
              />
            </li>
          )
        })}
      </ul>
      {projects.length === 0 && !creating && !projectsLoading && (
        projectsError ? (
          <ListProblem message={projectsError} onRetry={reloadLists} />
        ) : (
          <p className={styles.empty}>Group conversations and the files she can cite from them.</p>
        )
      )}

      <Popover
        anchor={anchor}
        open={menuFor !== null}
        onClose={() => setMenuFor(null)}
        placement="bottom-end"
        label="Project options"
      >
        {menuFor && (
          <Menu
            label="Project options"
            onDone={() => setMenuFor(null)}
            items={[
              {
                id: 'delete',
                label: 'Delete project',
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

      <Confirm
        open={deleting !== null}
        title="Delete this project?"
        confirmLabel="Delete project"
        cancelLabel="Keep it"
        destructive
        busy={busy}
        error={failed ? "Couldn't delete it just now. Try again in a moment." : null}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      >
        “{deleting?.name}” goes, but its conversations stay: they just won't be filed in a
        project any more.
      </Confirm>
    </section>
  )
}
