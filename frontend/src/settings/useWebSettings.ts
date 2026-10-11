// Web search as the backend holds it (ADR 0003): loaded when Settings opens, reloaded
// when the event lane reconnects, and followed live when it is changed anywhere else.
// The Brave key goes in and never comes back: only whether one is set.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { ApiError, apiFetch, failureMessage, messageFor } from '../lib/api'

export type SearchService = 'searxng' | 'brave'

export interface WebSettings {
  on: boolean
  service: SearchService
  searxngUrl: string
  blockedHosts: string[]
  keySet: boolean
}

interface WebPayload {
  web_search: boolean
  provider: SearchService
  searxng_url: string
  blocked_hosts: string[]
  brave_key_set: boolean
}

/** What trying the search service found: whether it answered, and if not, why. */
export type TestResult = { ok: true } | { ok: false; reason: string }

const LOAD_FAILED = "Couldn't load the web settings. Try again in a moment."
const SAVE_FAILED = "Couldn't save that. Try again in a moment."

function toSettings(body: WebPayload): WebSettings {
  return {
    on: body.web_search,
    service: body.provider,
    searxngUrl: body.searxng_url,
    blockedHosts: body.blocked_hosts,
    keySet: body.brave_key_set,
  }
}

function toPayload(settings: WebSettings): Omit<WebPayload, 'brave_key_set'> {
  return {
    web_search: settings.on,
    provider: settings.service,
    searxng_url: settings.searxngUrl,
    blocked_hosts: settings.blockedHosts,
  }
}

export function useWebSettings() {
  const [settings, setSettings] = useState<WebSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  // Only the newest load or save may write, as in usePersona.
  const latest = useRef(0)

  const load = useCallback(async () => {
    const mine = ++latest.current
    setLoadError(null)
    try {
      const response = await apiFetch('/connected')
      if (!response.ok) {
        const message = await failureMessage(response, LOAD_FAILED)
        if (mine === latest.current) setLoadError(message)
        return
      }
      const body = (await response.json()) as WebPayload
      if (mine === latest.current) setSettings(toSettings(body))
    } catch (error) {
      if (mine === latest.current) setLoadError(messageFor(error, LOAD_FAILED))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useAppEvents({ onWebSettingsChanged: () => void load(), onConnected: () => void load() })

  const send = useCallback(async (path: string, body: unknown): Promise<WebSettings> => {
    const mine = ++latest.current
    const response = await apiFetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new ApiError('rejected', await failureMessage(response, SAVE_FAILED))
    const stored = toSettings((await response.json()) as WebPayload)
    if (mine === latest.current) setSettings(stored)
    return stored
  }, [])

  /** Saves `changes` over what is shown; throws an ApiError whose message is safe to show. */
  const save = useCallback(
    (changes: Partial<WebSettings>) => {
      if (settings === null) return Promise.reject(new ApiError('rejected', SAVE_FAILED))
      return send('/connected', toPayload({ ...settings, ...changes }))
    },
    [send, settings],
  )

  const setKey = useCallback((key: string | null) => send('/connected/key', { key }), [send])

  const test = useCallback(async (): Promise<TestResult> => {
    try {
      const response = await apiFetch('/connected/test', { method: 'POST' })
      if (!response.ok) return { ok: false, reason: 'unreachable' }
      const body = (await response.json()) as { ok: boolean; reason?: string }
      return body.ok ? { ok: true } : { ok: false, reason: String(body.reason ?? 'unreachable') }
    } catch {
      return { ok: false, reason: 'offline' }
    }
  }, [])

  return { settings, loadError, retry: load, save, setKey, test }
}
