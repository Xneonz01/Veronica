// Events the desktop shell sends to the main window.
//
// Rust owns windows and shortcuts but not navigation. The tray and hotkeys emit a
// route id and the router maps it to a path, so paths stay out of the Rust code and
// adding a page doesn't touch it.

import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { isDesktop, listen } from './tauri'
import { ROUTES, type RouteId } from '../lib/routes'

/** Where the companion handed off a conversation, for `Chat` to pick up on mount. */
const HANDOFF_KEY = 'veronica:handoff-conversation'

export function takeHandoff(): number | null {
  const raw = sessionStorage.getItem(HANDOFF_KEY)
  if (raw === null) return null
  sessionStorage.removeItem(HANDOFF_KEY) // once — a reload should not re-open it
  const id = Number(raw)
  return Number.isFinite(id) && id > 0 ? id : null
}

/**
 * Open a conversation handed over from the companion.
 *
 * Chat is the landing route and is usually already mounted, and navigating to
 * `/chat` from `/chat` does not remount it. Reading the stash only on mount would miss
 * the handoff and leave the id in storage to reopen later, so Chat drains the stash
 * on mount (for when it was not mounted) and also listens for the event live.
 */
export function useConversationHandoff(open: (conversationId: number) => void): void {
  const onOpen = useRef(open)
  onOpen.current = open

  useEffect(() => {
    const handed = takeHandoff()
    if (handed !== null) onOpen.current(handed)
    if (!isDesktop()) return

    const stop = listen<number | null>('veronica://open-conversation', (conversationId) => {
      sessionStorage.removeItem(HANDOFF_KEY) // consumed here; don't reopen on a later mount
      if (conversationId) onOpen.current(conversationId)
    })
    return () => {
      void stop.then((unlisten) => unlisten())
    }
  }, [])
}

/** Mounted once in the shell. No-ops outside Tauri. */
export function useDesktopEvents(): void {
  const navigate = useNavigate()

  useEffect(() => {
    if (!isDesktop()) return
    const stops: Array<Promise<() => void>> = []

    stops.push(
      listen<string>('veronica://navigate', (route) => {
        const path = ROUTES[route as RouteId]
        if (path) navigate(path)
      }),
    )

    stops.push(
      listen<number | null>('veronica://open-conversation', (conversationId) => {
        // Stashed rather than pushed through the router: `Chat` owns the socket and
        // reads this on mount. Conversations are persisted, so the id is the whole
        // handoff; nothing else has to cross the window boundary.
        if (conversationId) sessionStorage.setItem(HANDOFF_KEY, String(conversationId))
        navigate(ROUTES.chat)
      }),
    )

    return () => {
      for (const stop of stops) void stop.then((unlisten) => unlisten())
    }
  }, [navigate])
}
