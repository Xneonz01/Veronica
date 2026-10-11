// The app-wide notification engine. Subscribes to the event lane (/ws/events), decides
// which memory writes deserve a toast and how loud, plays the matching sound, and holds
// the toast queue plus a bounded history. Mounted once in the app shell so every
// main-window surface, including the live call, sees the same notifications.

import { useCallback, useRef, useState, type ReactNode } from 'react'
import { playAction, type SoundAction } from '../audio/notificationSounds'
import { useAppEvents } from '../audio/useAppEvents'
import { NotificationContext } from './context'
import { shouldNotify, wantsToast } from './rules'
import { useNotificationSettings } from './useNotificationSettings'
import { apiFetch } from '../lib/api'
import type { MemoryEvent, Notification } from './types'

const MAX_VISIBLE = 4
const HISTORY_CAP = 30
const COALESCE_MS = 1500 // writes sharing a coalesce id within this window collapse into one
let counter = 0

function soundFor(notification: Notification): SoundAction {
  if (notification.action === 'removed') return 'forgotten'
  if (notification.action === 'added' && notification.source === 'user') return 'remembered'
  return 'updated'
}

function toNotification(event: MemoryEvent): Notification {
  return {
    id: `n${++counter}`,
    action: event.action,
    summary: event.summary,
    source: event.source,
    kind: event.kind,
    memoryId: event.memoryId,
    undoable: event.undoable,
    confidence: event.confidence,
    createdAt: counter,
    coalesceId: event.coalesceId,
    ts: event.ts,
    members: [],
  }
}

export function NotificationProvider({ children }: { children: ReactNode }) {
  const settings = useNotificationSettings()
  const settingsRef = useRef(settings)
  settingsRef.current = settings
  const [toasts, setToasts] = useState<Notification[]>([])
  const [history, setHistory] = useState<Notification[]>([])
  const [latest, setLatest] = useState<Notification | null>(null)
  const [unseen, setUnseen] = useState(0)
  const recent = useRef(new Map<string, number>())

  const push = useCallback((event: MemoryEvent) => {
    if (!shouldNotify(event)) return
    const { verbosity, sounds } = settingsRef.current
    const cid = event.coalesceId
    const now = performance.now()
    const followUp = cid !== undefined && now - (recent.current.get(cid) ?? -Infinity) < COALESCE_MS
    if (cid !== undefined) recent.current.set(cid, now)
    // Entries only matter for COALESCE_MS. Sweep old ones so the map doesn't grow by
    // one entry per burst for the whole session.
    for (const [key, at] of recent.current) {
      if (now - at > COALESCE_MS) recent.current.delete(key)
    }
    const parsed = toNotification(event)
    const notification = { ...parsed, toasted: !followUp && wantsToast(parsed, verbosity) }
    setHistory((cur) => [notification, ...cur].slice(0, HISTORY_CAP)) // always: feeds nav + log
    setUnseen((count) => count + 1)

    if (followUp) {
      // a burst (a session's learns) collapses: merge into the open toast, one tick only
      setToasts((cur) => {
        const index = cur.findIndex((t) => t.coalesceId === cid)
        if (index < 0) return cur
        const copy = [...cur]
        copy[index] = { ...copy[index], members: [...copy[index].members, notification] }
        return copy
      })
      return
    }

    if (sounds && verbosity !== 'silent') playAction(soundFor(notification))
    if (notification.toasted) {
      setToasts((cur) => [notification, ...cur].slice(0, MAX_VISIBLE))
      setLatest(notification) // the call reacts to this while one is live
    }
  }, [])

  useAppEvents({ onMemoryUpdated: push, onMemoryLearned: push })

  const dismiss = useCallback((id: string) => {
    setToasts((cur) => cur.filter((t) => t.id !== id))
  }, [])

  const undo = useCallback((notification: Notification) => {
    // Optimistic: the toast goes immediately. A failed undo must not look like a
    // success while the memory is still stored, so a failure notice is shown instead.
    setToasts((cur) => cur.filter((t) => t.id !== notification.id))
    void (async () => {
      try {
        const res = await apiFetch(`/memory/${notification.memoryId}/undo`, { method: 'POST' })
        if (res.ok) return
      } catch {
        // fall through to the failure notice
      }
      setToasts((cur) => [
        ...cur,
        {
          ...notification,
          id: `${notification.id}:undo-failed`,
          summary: "Couldn't undo that — it's still saved.",
          undoable: false,
        },
      ])
    })()
  }, [])

  const markSeen = useCallback(() => setUnseen(0), [])

  return (
    <NotificationContext.Provider
      value={{ toasts, history, latest, unseen, settings, dismiss, undo, markSeen }}
    >
      {children}
    </NotificationContext.Provider>
  )
}
