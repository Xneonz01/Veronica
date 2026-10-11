// Adding a document by pasting it: a title and the text. Kept open with its
// reason if it can't be added, so nothing typed is lost.

import { useRef, useState } from 'react'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { TextArea, TextField } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import styles from './PasteSheet.module.css'

export function PasteSheet({
  open,
  onClose,
  onAdd,
}: {
  open: boolean
  onClose: () => void
  onAdd: (title: string, text: string) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const titleField = useRef<HTMLInputElement>(null)
  const ready = title.trim() !== '' && text.trim() !== ''

  async function add() {
    setBusy(true)
    setFailed(null)
    try {
      await onAdd(title.trim(), text)
      setTitle('')
      setText('')
      onClose()
    } catch (error) {
      setFailed(messageFor(error, "Couldn't add that text. Try again in a moment."))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={busy ? () => undefined : onClose}
      title="Paste a document"
      description="She reads it, keeps it on this computer, and can cite it in her answers."
      size="lg"
      initialFocus={titleField}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void add()} disabled={!ready} loading={busy}>
            Add document
          </Button>
        </>
      }
    >
      <div className={styles.fields}>
        <TextField ref={titleField} label="Title" placeholder="Roof repair notes" value={title} onChange={(event) => setTitle(event.target.value)} />
        <TextArea label="Text" rows={10} value={text} onChange={(event) => setText(event.target.value)} />
        {failed && (
          <Notice tone="critical" live>
            {failed}
          </Notice>
        )}
      </div>
    </Sheet>
  )
}
