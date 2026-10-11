// The conversation's name at the top of the page, with the project it is filed
// in. Renaming, moving and deleting live on its row in the sidebar.

import { Folder } from 'lucide-react'
import styles from './ChatHeader.module.css'

export function ChatHeader({ title, project }: { title: string; project: string | null }) {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>{title}</h1>
      {project && (
        <span className={styles.project}>
          <Folder size={13} aria-hidden="true" />
          {project}
        </span>
      )}
    </header>
  )
}
