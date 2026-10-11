// The check of this computer (`GET /diagnostics/doctor`, `backend/app/doctor.py`), for
// About: run when the panel opens and again on request. Each check is a verdict, a code
// and the few values its words need; the words are the app's (`settings/computerCopy.ts`).

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { apiFetch, failureMessage, messageFor } from '../lib/api'
import { isRecord, readDetail } from '../shell/useProblems'

export type Verdict = 'ok' | 'warn' | 'fail' | 'unknown'

export interface ComputerCheck {
  check: string
  verdict: Verdict
  code: string
  detail: Record<string, string | number>
}

const FAILED = "Couldn't check this computer. Try again in a moment."
const VERDICTS: ReadonlySet<string> = new Set(['ok', 'warn', 'fail', 'unknown'])

// A failed response, carrying copy that `failureMessage` has already made safe to show.
class CheckError extends Error {}

export function useDoctor() {
  const [checks, setChecks] = useState<ComputerCheck[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)
  // Numbered, so an answer overtaken by a newer check can't replace it.
  const runs = useRef(0)
  const inFlight = useRef(false)
  // What makes the backend's coming back check again by itself: a check that failed
  // (most often with the backend away), a drop since the last check began (the backend
  // may have restarted with models installed or another llm.model), or its coming back
  // while a check was on its way that then failed. A check that worked, with the backend
  // there all along, waits to be asked.
  const failed = useRef(false)
  const dropped = useRef(false)
  const backDuring = useRef(false)

  /** Check again; the checks found, or null when the check failed or was overtaken. */
  const check = useCallback(async (): Promise<ComputerCheck[] | null> => {
    const run = ++runs.current
    inFlight.current = true
    dropped.current = false
    backDuring.current = false
    setChecking(true)
    setError(null)
    let found: ComputerCheck[] | null = null
    let failure: string | null = null
    try {
      const res = await apiFetch('/diagnostics/doctor')
      if (!res.ok) throw new CheckError(await failureMessage(res, FAILED))
      found = readChecks(await res.json())
    } catch (err) {
      failure = err instanceof CheckError ? err.message : messageFor(err, FAILED)
    }
    if (run !== runs.current) return null
    inFlight.current = false
    failed.current = found === null
    // A check that failed clears the last one: what it said may no longer be true.
    setChecks(found)
    setError(failure)
    setCheckedAt(found ? new Date() : null)
    setChecking(false)
    if (found === null && backDuring.current) void check()
    return found
  }, [])

  useEffect(() => {
    void check()
  }, [check])

  useAppEvents({
    onDisconnected: () => {
      dropped.current = true
    },
    onConnected: () => {
      if (failed.current || dropped.current) void check()
      else if (inFlight.current) backDuring.current = true
    },
  })

  return { checks, error, checking, checkedAt, check }
}

/** The checks in a response, keeping only what has the expected shape; an unknown
    verdict is taken as unknown, never as fine. A response with none is a failed check. */
export function readChecks(body: unknown): ComputerCheck[] {
  const list = isRecord(body) ? body.checks : undefined
  if (!Array.isArray(list)) throw new Error('no checks in the response')
  const checks = list.flatMap((item: unknown) => {
    if (!isRecord(item) || typeof item.check !== 'string' || typeof item.code !== 'string') return []
    const verdict = typeof item.verdict === 'string' && VERDICTS.has(item.verdict) ? (item.verdict as Verdict) : 'unknown'
    return [{ check: item.check, verdict, code: item.code, detail: readDetail(item.detail) }]
  })
  if (checks.length === 0) throw new Error('no checks in the response')
  return checks
}
