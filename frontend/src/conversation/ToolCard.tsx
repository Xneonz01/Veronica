// One of her actions inside an answer: what she did, in a line, and when there is
// more to it (what a recall found), the rest on opening. A failure says what she
// couldn't do and why, in her words.

import { AppWindow, BookmarkMinus, BookmarkPlus, Brain, ChevronDown, FilePen, FileSearch, FileText, FolderOpen, Globe, PenLine, Plug, Search, Wrench } from 'lucide-react'
import type { ToolRun } from '../chat/types'
import { fromToolServer, toolRunTitle } from '../lib/capabilities'
import { cx } from '../ui/cx'
import styles from './ToolCard.module.css'
import { presentResult } from './toolResult'

const ICONS: Record<string, typeof Wrench> = {
  files_search: FileSearch,
  files_read: FileText,
  files_list: FolderOpen,
  files_write: FilePen,
  apps_open: AppWindow,
  memory_save: BookmarkPlus,
  memory_recall: Brain,
  memory_correct: PenLine,
  memory_forget: BookmarkMinus,
  web_search: Search,
  web_read: Globe,
}

export function ToolCard({ run }: { run: ToolRun }) {
  const Icon = fromToolServer(run.name) ? Plug : (ICONS[run.name.replace(/\./g, '_')] ?? Wrench)
  const result = presentResult(run.name, run.result)
  const [peek, ...rest] = result.split('\n')
  const head = (
    <>
      <span className={styles.icon}>
        <Icon size={16} aria-hidden="true" />
      </span>
      <span className={styles.title}>{toolRunTitle(run.name, run.ok)}</span>
      {peek && <span className={styles.peek}>{peek}</span>}
    </>
  )

  // A one-line result is all said in the line: nothing to open.
  if (!rest.some((line) => line.trim())) {
    return (
      <div className={cx(styles.card, !run.ok && styles.failed)}>
        <div className={styles.head}>{head}</div>
      </div>
    )
  }
  // A native disclosure (DESIGN.md §8), in the card's own shape.
  return (
    <details className={cx(styles.card, !run.ok && styles.failed)}>
      <summary className={cx(styles.head, styles.opens)}>
        {head}
        <ChevronDown size={16} className={styles.chevron} aria-hidden="true" />
      </summary>
      <div className={styles.result}>{result}</div>
    </details>
  )
}
