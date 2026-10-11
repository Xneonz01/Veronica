// Files dropped into a conversation. Documents are indexed and become citable;
// images are only stored, since no vision model reads them (see `attachments.py`).

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch, failureMessage } from '../lib/api'

export interface Attachment {
  id: number
  conversation_id: number
  kind: 'document' | 'image'
  name: string
  size_bytes: number
  understood: boolean
}

export interface UseAttachments {
  items: Attachment[]
  uploading: string[]
  error: string | null
  add: (files: FileList | File[]) => Promise<void>
  remove: (id: number) => Promise<void>
  dismissError: () => void
}

export function useAttachments(conversationId: number | null): UseAttachments {
  const [items, setItems] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  // The conversation on screen now. The closure inside `add` only sees the one that
  // was current when the upload started, so a finished upload checks this ref to
  // learn whether the user has moved on.
  const viewing = useRef<number | null>(conversationId)
  viewing.current = conversationId

  useEffect(() => {
    setUploading([])  // in-flight chips belong to the conversation they were dropped in
    if (conversationId === null) {
      setItems([])
      return
    }
    let stale = false
    void (async () => {
      try {
        const response = await apiFetch(`/conversations/${conversationId}/attachments`)
        if (!response.ok) return
        const body = (await response.json()) as { attachments?: Attachment[] }
        if (!stale) setItems(body.attachments ?? [])
      } catch {
        // offline: leave the list as is; it is fetched again on the next conversation change
      }
    })()
    return () => {
      stale = true
    }
  }, [conversationId])

  const add = useCallback(
    async (files: FileList | File[]) => {
      // Pin the upload to the conversation it was dropped into, so switching
      // conversations mid-upload can't add the finished chip to the wrong list.
      const target = conversationId
      if (target === null) {
        // The conversation is created by the first message, so there is nothing to
        // attach to yet. Tell the user rather than silently doing nothing.
        setError('Send a message first, then you can attach files to this conversation.')
        return
      }
      for (const file of Array.from(files)) {
        setUploading((prev) => [...prev, file.name])
        try {
          const form = new FormData()
          form.append('file', file)
          const response = await apiFetch(`/conversations/${target}/attachments`, {
            method: 'POST',
            body: form,
          })
          if (!response.ok) {
            setError(await failureMessage(response, "I couldn't take that file."))
            continue
          }
          const body = (await response.json()) as { attachment: Attachment }
          // The upload still landed in `target`; it just must not be drawn under a
          // conversation the user has since switched to.
          if (viewing.current === target) {
            setItems((prev) =>
              prev.some((item) => item.id === body.attachment.id)
                ? prev
                : [...prev, body.attachment],
            )
          }
        } catch {
          setError("Can't reach Veronica right now.")
        } finally {
          setUploading((prev) => prev.filter((name) => name !== file.name))
        }
      }
    },
    [conversationId],
  )

  const remove = useCallback(
    async (id: number) => {
      if (conversationId === null) return
      try {
        const response = await apiFetch(`/conversations/${conversationId}/attachments/${id}`, {
          method: 'DELETE',
        })
        if (!response.ok) {
          setError(await failureMessage(response, "I couldn't remove that."))
          return
        }
        setItems((prev) => prev.filter((item) => item.id !== id))
      } catch {
        setError("Can't reach Veronica right now.")
      }
    },
    [conversationId],
  )

  const dismissError = useCallback(() => setError(null), [])
  return { items, uploading, error, add, remove, dismissError }
}
