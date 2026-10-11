// The conversation list: load, rename, delete. It holds only summaries; messages
// arrive over the socket when a conversation is opened, so this is cheap enough to
// refresh on every turn.

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch, failureMessage } from '../lib/api'
import type { ConversationSummary } from './types'

export interface UseConversations {
  items: ConversationSummary[]
  loading: boolean
  /** Why the list couldn't be loaded; a failed change is reported by its caller. */
  error: string | null
  refresh: () => Promise<void>
  /** False when the old title had to be put back, so the caller can say so. */
  rename: (id: number, title: string) => Promise<boolean>
  remove: (id: number) => Promise<boolean>
  /** File it in a project, or out of every project with null. */
  move: (id: number, projectId: number | null) => Promise<boolean>
}

export function useConversations(): UseConversations {
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // The list as last drawn: what a failed rename puts back.
  const shown = useRef(items)
  useEffect(() => {
    shown.current = items
  }, [items])

  const refresh = useCallback(async () => {
    try {
      const response = await apiFetch('/conversations')
      if (!response.ok) {
        setError(await failureMessage(response, "Couldn't load your conversations."))
        return
      }
      const body = (await response.json()) as { conversations?: ConversationSummary[] }
      setItems(body.conversations ?? [])
      setError(null)
    } catch {
      setError("Can't reach Veronica right now.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const rename = useCallback(
    async (id: number, title: string) => {
      // Optimistic: a rename is easy to revert, and a title that lags behind the edit
      // that set it feels broken. The old title is put back by hand as well as by a
      // reload, which can't reach the backend when the rename couldn't either.
      const before = shown.current.find((item) => item.id === id)?.title
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, title } : item)))
      const revert = () => {
        if (before !== undefined) {
          setItems((prev) => prev.map((item) => (item.id === id && item.title === title ? { ...item, title: before } : item)))
        }
        void refresh()
      }
      try {
        const response = await apiFetch(`/conversations/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title }),
        })
        if (response.ok) return true
      } catch {
        // offline: reverted below, like a refusal
      }
      revert()
      return false
    },
    [refresh],
  )

  const remove = useCallback(async (id: number): Promise<boolean> => {
    try {
      const response = await apiFetch(`/conversations/${id}`, { method: 'DELETE' })
      if (!response.ok) return false
      setItems((prev) => prev.filter((item) => item.id !== id))
      return true
    } catch {
      return false
    }
  }, [])

  const move = useCallback(async (id: number, projectId: number | null): Promise<boolean> => {
    try {
      const response = await apiFetch('/projects/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: id, project_id: projectId }),
      })
      if (!response.ok) return false
      setItems((prev) => prev.map((item) => (item.id === id ? { ...item, project_id: projectId } : item)))
      return true
    } catch {
      return false
    }
  }, [])

  return { items, loading, error, refresh, rename, remove, move }
}
