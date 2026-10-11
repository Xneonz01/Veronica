// "Keep this": saves the companion's text as a memory when you ask for it.
//
// Saving is its own action rather than inferred from the sentence. "The boiler
// service is due in March" could be a question or a note, and you should always
// know which one you are getting.
//
// The write goes through `POST /memory`, which calls `MemoryService.add_if_new`: a
// duplicate check in front of the same `MemoryService.add` the `memory_save` tool
// calls. So it emits the usual `memory.updated` event and is never a silent write.
//
// Undo lives here instead of in a toast stack: the companion sits outside the
// shell and has no notification provider, and mounting a second one would open
// another events socket for a small panel.

import { Bookmark, Check } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { apiFetch, failureMessage } from '../../lib/api'
import { Button } from '../../ui/Button'
import styles from './QuickSave.module.css'

interface Props {
  text: string
  onSaved: () => void
}

type Status = 'idle' | 'saving' | 'saved'

const UNDO_MS = 6000

export function QuickSave({ text, onSaved }: Props) {
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<number | null>(null)
  const empty = !text.trim()

  const undo = async () => {
    if (saved === null) return
    setSaved(null)
    setStatus('idle')
    try {
      await apiFetch(`/memory/${saved}/undo`, { method: 'POST' })
    } catch {
      setError("Couldn't take that back. It's on the Memory page.")
    }
  }

  const save = async () => {
    if (empty || status === 'saving') return
    setStatus('saving')
    setError(null)
    try {
      const response = await apiFetch('/memory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text.trim() }),
      })
      if (!response.ok) {
        // 409: she already knows it. Worth saying, but not a failure.
        setError(response.status === 409 ? 'Already remembered.' : await failureMessage(response, "Couldn't keep that."))
        setStatus('idle')
        return
      }
      const created = (await response.json()) as { id: number }
      setSaved(created.id)
      setStatus('saved')
      onSaved()
      // Time to notice a mistake: this is the only Undo the companion has.
      window.setTimeout(() => {
        setStatus('idle')
        setSaved(null)
      }, UNDO_MS)
    } catch {
      setError("Can't reach Veronica right now.")
      setStatus('idle')
    }
  }

  // The button's hint advertises Ctrl+Enter, so it is bound on the window.
  const latest = useRef(save)
  useLayoutEffect(() => {
    latest.current = save
  })
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault()
        void latest.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      {error && (
        <span className={styles.error} role="alert">
          {error}
        </span>
      )}
      {status === 'saved' && saved !== null ? (
        <span className={styles.kept}>
          <Check size={14} aria-hidden="true" />
          Kept
          <Button size="sm" variant="ghost" onClick={() => void undo()}>
            Undo
          </Button>
        </span>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          icon={<Bookmark size={14} aria-hidden="true" />}
          loading={status === 'saving'}
          disabled={empty}
          title="Keep this as a memory (Ctrl+Enter)"
          aria-keyshortcuts="Control+Enter"
          onClick={() => void save()}
        >
          Keep this
        </Button>
      )}
    </>
  )
}
