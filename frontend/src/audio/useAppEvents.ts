// Subscribes a surface to the app-wide event lane (/ws/events), so it reacts live to
// changes made anywhere (voices, memory, documents, confirmations), with or without
// an active call. Every surface shares one socket (lib/eventsLane.ts); handlers are
// read from a ref, so a re-render never re-subscribes.

import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { eventsLane, type Frame, type LaneStatus } from '../lib/eventsLane'
import { toMemoryEvent, type MemoryEvent } from '../notify/types'

interface Handlers {
  onVoiceChanged?: (voiceId: string) => void
  onVoicesUpdated?: () => void
  onMemoryUpdated?: (event: MemoryEvent) => void // a memory was added/edited/deleted/approved
  onMemoryLearned?: (event: MemoryEvent) => void // the learning loop stored or queued a fact
  onRagUpdated?: () => void // a document was indexed or removed
  onLearningMode?: (mode: string) => void // proactivity changed (here or elsewhere)
  onPersonaChanged?: (personality: string) => void // her personality was saved anywhere
  onProblemsChanged?: () => void // what /health reports changed after the start
  // A recovery key was made or used, anywhere: by this app (`via` "ui") or another program
  // ("api"), and when a made one was (`made`, empty for a use).
  onRecoveryUpdated?: (event: { via: string; made: string }) => void
  onWebSettingsChanged?: () => void // web search was switched or set up, anywhere
  onConnected?: () => void // fires on (re)connect — lets a surface resync after a drop
  onDisconnected?: () => void // the lane dropped — surfaces show a calm degraded state
  // A guarded capability is waiting on the user. Blocking, not a toast.
  onConfirmRequired?: (payload: Record<string, unknown>) => void
  onConfirmExpired?: (token: string) => void
  // Answered somewhere else, on screen or out loud during a call. Either way the
  // prompt is spent, and its card should close without waiting for the countdown.
  onConfirmResolved?: (token: string) => void
}

export function useAppEvents(handlers: Handlers): void {
  const ref = useRef(handlers)
  useLayoutEffect(() => {
    ref.current = handlers
  })

  useEffect(
    () =>
      eventsLane.subscribe({
        onFrame: (frame) => dispatch(frame, ref.current),
        onConnected: () => ref.current.onConnected?.(),
        onDisconnected: () => ref.current.onDisconnected?.(),
      }),
    [],
  )
}

/** The backend's reachability as the event lane sees it. Keeps the lane open. */
export function useLaneStatus(): LaneStatus {
  useEffect(() => eventsLane.subscribe({}), [])
  return useSyncExternalStore(
    (listener) => eventsLane.watch(listener),
    () => eventsLane.status(),
  )
}

function dispatch(msg: Frame, handlers: Handlers) {
  const type = String(msg.type ?? '')
  if (type === 'voice_changed') handlers.onVoiceChanged?.(String(msg.voice_id ?? ''))
  else if (type === 'voices_updated') handlers.onVoicesUpdated?.()
  else if (type === 'memory_updated') handlers.onMemoryUpdated?.(toMemoryEvent(msg))
  else if (type === 'memory_learned') handlers.onMemoryLearned?.(toMemoryEvent(msg))
  else if (type === 'rag_updated') handlers.onRagUpdated?.()
  else if (type === 'learning_mode') handlers.onLearningMode?.(String(msg.mode ?? ''))
  else if (type === 'persona_changed' && typeof msg.personality === 'string') handlers.onPersonaChanged?.(msg.personality)
  else if (type === 'problems_changed') handlers.onProblemsChanged?.()
  else if (type === 'recovery_updated')
    handlers.onRecoveryUpdated?.({ via: String(msg.via ?? ''), made: String(msg.made ?? '') })
  else if (type === 'connected_changed') handlers.onWebSettingsChanged?.()
  else if (type === 'capability_confirm_required') handlers.onConfirmRequired?.(msg)
  else if (type === 'capability_confirm_expired') handlers.onConfirmExpired?.(String(msg.token ?? ''))
  else if (type === 'capability_confirm_resolved') handlers.onConfirmResolved?.(String(msg.token ?? ''))
}
