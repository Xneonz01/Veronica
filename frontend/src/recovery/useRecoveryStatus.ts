// Whether her recovery key is made, for Settings > Privacy: read when the panel shows,
// again when one is made or used in any window (`recovery_updated`), and when the
// backend comes back after a failed read or a drop.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { apiFetch, failureMessage, messageFor } from '../lib/api'
import { isRecord } from '../shell/useProblems'

/** `made` when one recovery key seals all her data keys; `partial` when only some are
    (a key made since); `unavailable` says why none can be made now. */
export type RecoveryStatus = {
  made: string | null
  partial: boolean
  unavailable: 'unsupported' | 'locked' | 'nothing' | null
}

const LOAD_FAILED = "Couldn't read whether she has a recovery key. Try again in a moment."
const REASONS = new Set(['unsupported', 'locked', 'nothing'])

export function useRecoveryStatus() {
  const [status, setStatus] = useState<RecoveryStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const failed = useRef(false)
  const dropped = useRef(false)

  const load = useCallback(async () => {
    dropped.current = false
    setError(null)
    try {
      const res = await apiFetch('/recovery-key')
      if (!res.ok) {
        failed.current = true
        setError(await failureMessage(res, LOAD_FAILED))
        return
      }
      failed.current = false
      setStatus(readStatus(await res.json()))
    } catch (err) {
      failed.current = true
      setError(messageFor(err, LOAD_FAILED))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useAppEvents({
    onRecoveryUpdated: () => void load(),
    onDisconnected: () => {
      dropped.current = true
    },
    onConnected: () => {
      if (failed.current || dropped.current) void load()
    },
  })

  return { status, error, reload: load }
}

function readStatus(body: unknown): RecoveryStatus {
  const made = isRecord(body) && typeof body.made === 'string' ? body.made : null
  const unavailable =
    isRecord(body) && typeof body.unavailable === 'string' && REASONS.has(body.unavailable)
      ? (body.unavailable as RecoveryStatus['unavailable'])
      : null
  return { made, partial: isRecord(body) && body.partial === true, unavailable }
}
