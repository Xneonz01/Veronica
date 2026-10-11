// Knowledge documents hook: list / add (file or pasted text) / remove RAG documents.
// add* throw on failure so the composer can surface the typed reason inline.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { CallError } from '../audio/useRealtimeCall'
import { ApiError, apiFetch, failureMessage } from '../lib/api'
import type { DocumentRow } from './types'


export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<CallError | null>(null)

  const seq = useRef(0)

  const refresh = useCallback(async () => {
    // A burst of events can fire several refreshes that resolve out of order. Only the
    // newest request writes state, so an older snapshot can't land last and revert an
    // optimistic edit.
    const mine = ++seq.current
    try {
      const res = await apiFetch(`/rag/documents`)
      if (!res.ok) {
        const message = await failureMessage(res, "Couldn't load your documents. Try again in a moment.")
        if (mine === seq.current) setError({ kind: 'documents_unreachable', severity: 'fatal', message })
        return
      }
      const data = (await res.json()) as { documents: DocumentRow[] }
      if (mine !== seq.current) return
      setDocuments(data.documents)
      setError((cur) => (cur?.kind === 'documents_unreachable' ? null : cur))
    } catch {
      // Guarded too: a stale rejection landing after a newer successful refresh would
      // otherwise paint a fatal "unreachable" banner over fresh, correct data.
      if (mine === seq.current) {
        setError({
          kind: 'documents_unreachable',
          severity: 'fatal',
          message: "Couldn't reach the knowledge store — is the backend running?",
        })
      }
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const addFile = useCallback(
    async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      const res = await apiFetch(`/rag/documents`, { method: 'POST', body: form })
      if (!res.ok) throw new ApiError('request_failed', await failureMessage(res, 'That document could not be indexed.'))
      await refresh()
    },
    [refresh],
  )

  const addText = useCallback(
    async (title: string, text: string) => {
      const res = await apiFetch(`/rag/documents/text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, text }),
      })
      if (!res.ok) throw new ApiError('request_failed', await failureMessage(res, 'That text could not be indexed.'))
      await refresh()
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: number) => {
      setDocuments((ds) => ds.filter((d) => d.id !== id)) // optimistic
      try {
        const res = await apiFetch(`/rag/documents/${id}`, { method: 'DELETE' })
        if (!res.ok) {
          setError({ kind: 'doc_delete_failed', severity: 'recoverable', message: await failureMessage(res, 'Could not remove that document.') })
          await refresh()
        }
      } catch {
        setError({ kind: 'doc_delete_failed', severity: 'recoverable', message: 'Could not remove that document.' })
        await refresh()
      }
    },
    [refresh],
  )

  return { documents, loading, error, setError, refresh, addFile, addText, remove }
}
