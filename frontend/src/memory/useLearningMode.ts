// Learning-mode hook: read and change how learned facts are saved (`proactive` stores
// confident facts directly, `review_only` queues everything for approval). `setMode`
// is exposed so the page can apply a mode change that arrives over the event lane.

import { useCallback, useEffect, useState } from 'react'
import type { CallError } from '../audio/useRealtimeCall'
import { apiFetch } from '../lib/api'
import type { LearningMode } from './types'

export function useLearningMode() {
  const [mode, setMode] = useState<LearningMode>('proactive')
  const [error, setError] = useState<CallError | null>(null)

  const refresh = useCallback(async () => {
    try {
      const res = await apiFetch('/memory/learning-mode')
      if (!res.ok) return
      const data = (await res.json()) as { mode: LearningMode }
      setMode(data.mode)
    } catch {
      // non-fatal: the toggle shows the default until the backend answers
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const update = useCallback(
    async (next: LearningMode) => {
      const previous = mode
      setMode(next) // optimistic
      try {
        const res = await apiFetch('/memory/learning-mode', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: next }),
        })
        if (!res.ok) {
          setMode(previous)
          setError({ kind: 'mode_failed', severity: 'recoverable', message: 'Could not change the learning mode.' })
        }
      } catch {
        setMode(previous)
        setError({ kind: 'mode_failed', severity: 'recoverable', message: 'Could not change the learning mode.' })
      }
    },
    [mode],
  )

  return { mode, setMode, error, setError, refresh, update }
}
