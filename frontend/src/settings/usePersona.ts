// Her personality as the backend holds it: loaded when Settings opens, reloaded
// when the event lane reconnects, and followed live when it is saved anywhere else.
// Saving throws an ApiError whose message is safe to show: the backend refuses a
// personality that is too long or looks like it holds a secret, and says why.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { ApiError, apiFetch, failureMessage, messageFor } from '../lib/api'

export interface Persona {
  personality: string
  /** The personality she was made with. */
  original: string
  maxChars: number
  /** The fixed instructions around the personality, shown read-only. */
  scaffold: string
}

interface PersonaPayload {
  personality: string
  default: string
  max_chars: number
  scaffold_preview: string
}

const LOAD_FAILED = "Couldn't load her personality. Try again in a moment."
const SAVE_FAILED = "Couldn't save that. Try again in a moment."

function toPersona(body: PersonaPayload): Persona {
  return {
    personality: body.personality,
    original: body.default,
    maxChars: body.max_chars,
    scaffold: body.scaffold_preview,
  }
}

export function usePersona() {
  const [persona, setPersona] = useState<Persona | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  // Every load and save takes a number, and only the newest may write: a load that
  // was answered after a save would otherwise put the old text back.
  const latest = useRef(0)

  const load = useCallback(async () => {
    const mine = ++latest.current
    // Cleared first, so "Try again" that fails again is shown and said again.
    setLoadError(null)
    try {
      const response = await apiFetch('/persona')
      if (!response.ok) {
        const message = await failureMessage(response, LOAD_FAILED)
        if (mine === latest.current) setLoadError(message)
        return
      }
      const body = (await response.json()) as PersonaPayload
      if (mine === latest.current) setPersona(toPersona(body))
    } catch (error) {
      if (mine === latest.current) setLoadError(messageFor(error, LOAD_FAILED))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useAppEvents({
    onPersonaChanged: (personality) => setPersona((current) => current && { ...current, personality }),
    onConnected: () => void load(),
  })

  /** Resolves to what was stored, which the backend may have tidied (it trims). */
  const save = useCallback(async (personality: string): Promise<Persona> => {
    const mine = ++latest.current
    const response = await apiFetch('/persona', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ personality }),
    })
    if (!response.ok) throw new ApiError('rejected', await failureMessage(response, SAVE_FAILED))
    const stored = toPersona((await response.json()) as PersonaPayload)
    if (mine === latest.current) setPersona(stored)
    return stored
  }, [])

  return { persona, loadError, retry: load, save }
}
