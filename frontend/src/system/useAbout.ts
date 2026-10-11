// The running version, the patch notes and whether the last run ended cleanly, for
// the About panel in Settings. Loaded when the panel mounts, and again when the backend
// comes back after a failed load or a drop.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { apiFetch, failureMessage, messageFor } from '../lib/api'

export type NoteSection = 'new' | 'improved' | 'fixed' | 'known'

export interface ReleaseNotes {
  version: string
  label: string
  date: string
  channel: string
  sections: Partial<Record<NoteSection, string[]>>
}

export interface About {
  version: string
  label: string
  channel: string
  // False when the previous run of the backend never recorded a clean shutdown.
  previousRunClean: boolean | null
  releases: ReleaseNotes[]
}

interface DiagnosticsBody {
  version?: string
  label?: string
  channel?: string
  previous_run?: { clean_exit?: boolean | null } | null
}

const LOAD_FAILED = "Couldn't load the version details. Try again in a moment."

// A failed response, carrying copy that `failureMessage` has already made safe to show.
class LoadError extends Error {}

async function readJson<T>(path: string): Promise<T> {
  const res = await apiFetch(path)
  if (!res.ok) throw new LoadError(await failureMessage(res, LOAD_FAILED))
  return (await res.json()) as T
}

export function useAbout() {
  const [about, setAbout] = useState<About | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Loaded again by itself when the backend comes back: after a load that failed, and
  // after any drop, since a restart can be an update with notes and a version of its own.
  const failed = useRef(false)
  const dropped = useRef(false)

  const load = useCallback(async () => {
    dropped.current = false
    setError(null)
    try {
      const [notes, diagnostics] = await Promise.all([
        readJson<{ entries: ReleaseNotes[] }>('/changelog'),
        readJson<DiagnosticsBody>('/diagnostics'),
      ])
      const newest = notes.entries[0]
      failed.current = false
      setAbout({
        version: diagnostics.version ?? newest?.version ?? '',
        label: diagnostics.label ?? newest?.label ?? '',
        channel: diagnostics.channel ?? 'dev',
        previousRunClean: diagnostics.previous_run?.clean_exit ?? null,
        releases: notes.entries,
      })
    } catch (err) {
      failed.current = true
      setError(err instanceof LoadError ? err.message : messageFor(err, LOAD_FAILED))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useAppEvents({
    onDisconnected: () => {
      dropped.current = true
    },
    onConnected: () => {
      if (failed.current || dropped.current) void load()
    },
  })

  return { about, error, retry: load }
}
