// Memory data hook: list / edit / pin / delete + approve|reject candidates over the
// REST API, with optimistic updates. Live changes arrive via useAppEvents (memory.*).

import { useCallback, useEffect, useRef, useState } from 'react'
import type { CallError } from '../audio/useRealtimeCall'
import { apiFetch, failureMessage } from '../lib/api'
import type { MemoryRow } from './types'

const UNREACHABLE: CallError = {
  kind: 'memory_unreachable',
  severity: 'fatal',
  message: "Couldn't reach Veronica's memory — is the backend running?",
}


export interface MemoryPatch {
  content?: string
  importance?: number
  pinned?: boolean
}

export function useMemory() {
  const [memories, setMemories] = useState<MemoryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<CallError | null>(null)

  const seq = useRef(0)

  const refresh = useCallback(async () => {
    // A burst of events can fire several refreshes that resolve out of order. Only the
    // newest request writes state, so an older snapshot can't land last and revert an
    // optimistic edit.
    const mine = ++seq.current
    try {
      const res = await apiFetch(`/memory`)
      if (!res.ok) {
        // The backend answered, so "is it running?" would send you the wrong way.
        const message = await failureMessage(res, "Couldn't load her memories. Try again in a moment.")
        if (mine === seq.current) setError({ ...UNREACHABLE, message })
        return
      }
      const data = (await res.json()) as { memories: MemoryRow[] }
      if (mine !== seq.current) return
      setMemories(data.memories)
      setError((cur) => (cur?.kind === 'memory_unreachable' ? null : cur))
    } catch {
      if (mine === seq.current) setError(UNREACHABLE)
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const edit = useCallback(
    async (id: number, patch: MemoryPatch) => {
      setMemories((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m))) // optimistic
      try {
        const res = await apiFetch(`/memory/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        })
        if (!res.ok) {
          setError({ kind: 'edit_failed', severity: 'recoverable', message: await failureMessage(res, 'That change could not be saved.') })
          await refresh()
        }
      } catch {
        setError({ kind: 'edit_failed', severity: 'recoverable', message: 'That change could not be saved — is the backend running?' })
        await refresh()
      }
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: number) => {
      setMemories((ms) => ms.filter((m) => m.id !== id)) // optimistic
      try {
        const res = await apiFetch(`/memory/${id}`, { method: 'DELETE' })
        if (!res.ok) {
          setError({ kind: 'delete_failed', severity: 'recoverable', message: await failureMessage(res, 'Could not delete that memory.') })
          await refresh()
        }
      } catch {
        setError({ kind: 'delete_failed', severity: 'recoverable', message: 'Could not delete that memory.' })
        await refresh()
      }
    },
    [refresh],
  )

  const review = useCallback(
    async (id: number, action: 'approve' | 'reject') => {
      setMemories((ms) => ms.filter((m) => m.id !== id)) // leaves the review queue either way
      try {
        const res = await apiFetch(`/memory/${id}/${action}`, { method: 'POST' })
        if (!res.ok) {
          setError({ kind: 'review_failed', severity: 'recoverable', message: await failureMessage(res, 'Could not update that candidate.') })
        }
      } catch {
        setError({ kind: 'review_failed', severity: 'recoverable', message: 'Could not update that candidate.' })
      }
      await refresh() // an approved candidate becomes active — pull the true state
    },
    [refresh],
  )

  return { memories, loading, error, setError, refresh, edit, remove, review }
}
