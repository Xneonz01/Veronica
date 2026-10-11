// The documents she can cite: add one from a file or by pasting text, and remove
// one after asking. An answer that already quoted a document keeps its quote.

import { ClipboardPaste, FileText, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { messageFor } from '../lib/api'
import { Button, IconButton } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { Notice, Working } from '../ui/Feedback'
import styles from './DocumentList.module.css'
import { mimeLabel, relativeTime } from './format'
import rows from './MemoryList.module.css'
import { PasteSheet } from './PasteSheet'
import type { DocumentRow } from './types'

export function DocumentList({
  documents,
  onAddFile,
  onAddText,
  onRemove,
}: {
  documents: readonly DocumentRow[]
  onAddFile: (file: File) => Promise<void>
  onAddText: (title: string, text: string) => Promise<void>
  onRemove: (id: number) => void
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [reading, setReading] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [pasting, setPasting] = useState(false)
  const [removing, setRemoving] = useState<DocumentRow | null>(null)

  async function addFile(file: File | undefined) {
    if (!file) return
    setReading(file.name)
    setFailed(null)
    try {
      await onAddFile(file)
    } catch (error) {
      setFailed(messageFor(error, `Couldn't read “${file.name}”.`))
    } finally {
      setReading(null)
      if (picker.current) picker.current.value = ''
    }
  }

  return (
    <div className={styles.documents}>
      <div className={styles.toolbar}>
        <input ref={picker} type="file" accept=".txt,.md,.markdown,.pdf" hidden onChange={(event) => void addFile(event.target.files?.[0])} />
        <Button icon={<Upload size={16} aria-hidden="true" />} disabled={reading !== null} onClick={() => picker.current?.click()}>
          Add a file
        </Button>
        <Button variant="ghost" icon={<ClipboardPaste size={16} aria-hidden="true" />} onClick={() => setPasting(true)}>
          Paste text
        </Button>
        <span className={styles.accepts}>Text, Markdown or PDF</span>
      </div>

      {reading && <Working>Reading “{reading}”</Working>}
      {failed && (
        <Notice tone="critical" live action={<Button size="sm" variant="ghost" onClick={() => setFailed(null)}>OK</Button>}>
          {failed}
        </Notice>
      )}

      {documents.length > 0 && (
        <ul className={rows.list}>
          {documents.map((document) => (
            <li key={document.id} className={rows.row}>
              <FileText size={18} className={styles.icon} aria-hidden="true" />
              <div className={rows.text}>
                <p className={rows.content}>{document.title}</p>
                <p className={rows.meta}>
                  <span>
                    {document.chunk_count} {document.chunk_count === 1 ? 'passage' : 'passages'}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span>{mimeLabel(document.mime)}</span>
                  <span aria-hidden="true">·</span>
                  <span>Added {relativeTime(document.added_at) ?? 'earlier'}</span>
                </p>
              </div>
              <IconButton
                size="sm"
                className={rows.more}
                label={`Remove “${document.title}”`}
                icon={<Trash2 size={16} aria-hidden="true" />}
                onClick={() => setRemoving(document)}
              />
            </li>
          ))}
        </ul>
      )}

      <PasteSheet open={pasting} onClose={() => setPasting(false)} onAdd={onAddText} />

      <Confirm
        open={removing !== null}
        title="Remove this document?"
        confirmLabel="Remove"
        cancelLabel="Keep it"
        destructive
        onConfirm={() => {
          if (removing) onRemove(removing.id)
          setRemoving(null)
        }}
        onCancel={() => setRemoving(null)}
      >
        She won't cite “{removing?.title}” any more. Answers that already quoted it keep their quotes.
      </Confirm>
    </div>
  )
}
