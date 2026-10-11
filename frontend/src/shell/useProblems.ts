// What keeps her from working fully, as /health reports it (`backend/app/core/problems.py`):
// a code the app has words for, and the few values those words need. It is read each
// time the event lane reaches the backend, and again when the backend says the problems
// changed (one found after the start, or put right without a restart); it is let go
// when the lane drops, which the offline banner covers.

import { useCallback, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { apiFetch } from '../lib/api'

export type Problem = { code: string; detail: Record<string, string | number> }

export function useProblems(): { problems: Problem[]; refresh: () => void } {
  const [problems, setProblems] = useState<Problem[]>([])
  // Numbered, so a read still on its way when the lane drops, or overtaken by a newer
  // one, can't describe a backend that has gone.
  const reads = useRef(0)
  const refresh = useCallback(() => {
    const read = ++reads.current
    void fetchProblems().then((found) => {
      if (read === reads.current) setProblems(found)
    })
  }, [])
  useAppEvents({
    onConnected: refresh,
    onProblemsChanged: refresh,
    onDisconnected: () => {
      reads.current += 1
      setProblems([])
    },
  })
  return { problems, refresh }
}

/** None when /health can't be read: the lane reconnecting reads it again. */
export async function fetchProblems(): Promise<Problem[]> {
  try {
    const res = await apiFetch('/health')
    return res.ok ? readProblems(await res.json()) : []
  } catch {
    return []
  }
}

/** The problems in a /health body, keeping only what has the expected shape. */
export function readProblems(body: unknown): Problem[] {
  const problems = isRecord(body) ? body.problems : undefined
  if (!Array.isArray(problems)) return []
  return problems.flatMap((item: unknown) =>
    isRecord(item) && typeof item.code === 'string' ? [{ code: item.code, detail: readDetail(item.detail) }] : [],
  )
}

/** The strings and finite numbers of a `detail`, the only values the backend sends in one. */
export function readDetail(detail: unknown): Problem['detail'] {
  if (!isRecord(detail)) return {}
  const kept: Problem['detail'] = {}
  for (const [key, value] of Object.entries(detail)) {
    if (typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))) kept[key] = value
  }
  return kept
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
