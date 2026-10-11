// Projects: named workspaces that narrow what the assistant retrieves.
//
// The active project is held here and passed to the socket on `open`, so a
// conversation started inside a project belongs to it and only sees its documents.

import { useCallback, useEffect, useState } from 'react'
import { apiFetch, failureMessage } from '../lib/api'

export interface Project {
  id: number
  name: string
  conversation_count: number
}

export interface UseProjects {
  items: Project[]
  /** True until the first answer from the server, so no empty state flashes. */
  loading: boolean
  activeId: number | null
  /** Why the list couldn't be loaded; a failed change is reported by its caller. */
  error: string | null
  setActive: (id: number | null) => void
  refresh: () => Promise<void>
  create: (name: string) => Promise<Project | null>
  /** False when it wasn't deleted, so the caller can say so. */
  remove: (id: number) => Promise<boolean>
}

export function useProjects(): UseProjects {
  const [items, setItems] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await apiFetch('/projects')
      if (!response.ok) {
        setError(await failureMessage(response, "Couldn't load your projects."))
        return
      }
      const body = (await response.json()) as { projects?: Project[] }
      setItems(body.projects ?? [])
      setError(null)
    } catch {
      // Offline: the list keeps what it had until the next refresh.
      setError("Can't reach Veronica right now.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const create = useCallback(
    async (name: string): Promise<Project | null> => {
      try {
        const response = await apiFetch('/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        })
        if (!response.ok) return null
        const body = (await response.json()) as { project: Project }
        setItems((prev) => [...prev, body.project])
        return body.project
      } catch {
        return null
      }
    },
    [],
  )

  const remove = useCallback(
    async (id: number): Promise<boolean> => {
      try {
        const response = await apiFetch(`/projects/${id}`, { method: 'DELETE' })
        if (!response.ok) return false
        setItems((prev) => prev.filter((item) => item.id !== id))
        // Deleting the active project falls back to no project.
        setActiveId((current) => (current === id ? null : current))
        return true
      } catch {
        return false
      }
    },
    [],
  )

  return { items, loading, activeId, error, setActive: setActiveId, refresh, create, remove }
}
