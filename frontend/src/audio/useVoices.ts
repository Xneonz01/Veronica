// Voice registry data hook: list / activate / preview / create / delete over the
// REST API, with optimistic updates. Live changes arrive via useAppEvents.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { CallError } from './useRealtimeCall'
import { ApiError, apiFetch, failureMessage, messageFor } from '../lib/api'


export interface VoiceInfo {
  voice_id: string
  name: string
  source: string
  created_at: string
  active: boolean
}

const REGISTRY_ERROR: CallError = {
  kind: 'voices_unreachable',
  severity: 'fatal',
  message: "Couldn't reach the voice registry — is the backend running?",
}


export function useVoices() {
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<CallError | null>(null)
  const seq = useRef(0)

  const refresh = useCallback(async () => {
    // Events can fire refreshes that answer out of order; only the newest may write,
    // or an older list could land last and undo a switch that just happened.
    const mine = ++seq.current
    try {
      const res = await apiFetch(`/voices`)
      if (!res.ok) {
        const message = await failureMessage(res, "Couldn't load the voices. Try again in a moment.")
        if (mine === seq.current) setError({ ...REGISTRY_ERROR, message })
        return
      }
      const data = (await res.json()) as { voices: VoiceInfo[] }
      if (mine !== seq.current) return
      setVoices(data.voices)
      setError((cur) => (cur?.kind === 'voices_unreachable' ? null : cur))
    } catch {
      if (mine === seq.current) setError(REGISTRY_ERROR)
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const activate = useCallback(
    async (id: string) => {
      setVoices((vs) => vs.map((v) => ({ ...v, active: v.voice_id === id }))) // optimistic
      try {
        const res = await apiFetch(`/voices/${id}/activate`, { method: 'POST' })
        if (!res.ok) {
          setError({ kind: 'activate_failed', severity: 'recoverable', message: await failureMessage(res, 'Could not switch voice.') })
          await refresh() // the switch didn't take — resync to the true active voice
        }
      } catch {
        setError({ kind: 'activate_failed', severity: 'recoverable', message: 'Could not switch voice — is the backend running?' })
        await refresh()
      }
    },
    [refresh],
  )

  const create = useCallback(
    async (name: string, files: File[], source: 'upload' | 'record') => {
      const form = new FormData()
      form.append('name', name)
      form.append('consent', 'true')
      form.append('source', source)
      for (const file of files) form.append('files', file)
      const res = await apiFetch(`/voices`, { method: 'POST', body: form })
      if (!res.ok) throw new ApiError('request_failed', await failureMessage(res, 'That voice could not be created.'))
      await refresh()
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: string) => {
      // Callers invoke this through a void-typed prop, so a rejection would go
      // unhandled and the user couldn't tell whether the voice was deleted. Failures
      // are reported through `error` instead.
      try {
        const res = await apiFetch(`/voices/${id}`, { method: 'DELETE' })
        if (!res.ok) {
          setError({
            kind: 'delete_failed',
            severity: 'recoverable',
            message: await failureMessage(res, 'Could not delete that voice.'),
          })
          return
        }
        await refresh()
      } catch (err) {
        setError({
          kind: 'delete_failed',
          severity: 'recoverable',
          message: messageFor(err, 'Could not delete that voice.'),
        })
      }
    },
    [refresh],
  )

  const preview = useCallback(async (id: string) => {
    const res = await apiFetch(`/voices/${id}/preview`, { method: 'POST' })
    if (!res.ok) throw new ApiError('request_failed', await failureMessage(res, 'Preview failed — try again.'))
    const url = URL.createObjectURL(await res.blob())
    const audio = new Audio(url)
    audio.onended = () => URL.revokeObjectURL(url)
    try {
      await audio.play()
    } catch (err) {
      URL.revokeObjectURL(url) // it never played, so `ended` never comes to free it
      throw err
    }
  }, [])

  return { voices, loading, error, setError, refresh, activate, create, remove, preview }
}
