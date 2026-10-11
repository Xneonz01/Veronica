// The /ws/chat connection: sending, Stop, opening a conversation, and reconnect.
// What each frame that arrives does is `frames.ts`.
//
// Reconnects with the same backoff pattern as `useAppEvents`, so a backend restart
// doesn't leave a composer that looks fine but sends into a closed socket. The lane's
// state is exposed so the surface can show a banner while it is offline.

import { useCallback, useEffect, useRef, useState } from 'react'
import { WS } from '../lib/api'
import { handleFrame, type Frame } from './frames'
import { interrupt } from './transcript'
import type { ChatMessage, ChatState, ErrorDetail, LaneState, UseChat } from './types'

const WS_URL = `${WS}/ws/chat`
const RETRY_MS = [1000, 2000, 4000, 8000, 15000] as const
const REFUSED_RETRY_MS = RETRY_MS[RETRY_MS.length - 1]
// Far past any window (the backend reads a few tens of thousands of characters), and
// under the socket's 16 MiB a message, which a longer one closed, losing it: refused
// here, before it goes.
const MAX_SEND_CHARS = 1_000_000

export function useChat(): UseChat {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [state, setState] = useState<ChatState>('idle')
  const [lane, setLane] = useState<LaneState>('connecting')
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [opening, setOpening] = useState(false)
  const [errorKind, setErrorKind] = useState<string | null>(null)
  const [errorDetail, setErrorDetail] = useState<ErrorDetail>({})
  const [announcement, setAnnouncement] = useState('')
  const [returned, setReturned] = useState<{ text: string } | null>(null)
  // What `send` last sent, until a retry: only that can be sent back to the box.
  const lastSent = useRef<string | null>(null)

  const socket = useRef<WebSocket | null>(null)
  // Survives reconnects: the conversation on screen must be re-opened on the new
  // socket, or new messages would go to a backend session that knows nothing of it.
  const current = useRef<number | null>(null)
  // The project is sent with `open` and remembered for reconnects: a socket that came
  // back without it would silently widen what retrieval can search.
  const project = useRef<number | null>(null)
  const localId = useRef(-1)
  // Between Stop and the backend's `stopped`, frames already on their way belong to
  // the answer being stopped; drawn, they would bring it back.
  const discarding = useRef(false)
  // Opens sent and not yet answered. Until the newest is, every frame belongs to what
  // was on screen before: the turn the open cancelled, its `conversation`, its error.
  const pendingOpens = useRef(0)
  // The local id the next message gets, as of the newest open: messages from there
  // on were sent after it (the palette's Ask), and outlive its answer.
  const openMark = useRef(-1)
  // A turn in flight when the socket drops is reported, not silently lost.
  const inFlight = useRef(false)
  useEffect(() => {
    inFlight.current = state !== 'idle'
  }, [state])
  // The socket's first open has been answered: the backend takes chat on it.
  const ready = useRef(false)
  // The last socket to close was refused, until an open is answered: what can't be
  // sent meanwhile is said as the pause, not as a connection that is down.
  const refused = useRef(false)
  const retryNow = useRef<() => void>(() => undefined)

  /** Send only when the socket is open. `send()` on a connecting socket throws
   *  InvalidStateError, which would abort the click handler while the lane is
   *  reconnecting. On a closed socket the payload is silently dropped, which is why
   *  `open` updates its refs first: the reconnect re-sends the right conversation.
   *  Anything but an `open` also waits for the socket's first open to be answered. */
  const trySend = useCallback((payload: Record<string, unknown>): boolean => {
    const ws = socket.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    if (payload.action !== 'open' && !ready.current) return false
    ws.send(JSON.stringify(payload))
    return true
  }, [])

  const send = useCallback(
    (text: string): boolean => {
      const trimmed = text.trim()
      if (!trimmed) return false
      if (trimmed.length > MAX_SEND_CHARS) {
        setErrorKind('message_too_long') // the caller keeps the text
        return false
      }
      if (!trySend({ action: 'message', text: trimmed })) {
        setErrorKind(refused.current ? 'memory_unavailable' : 'offline')
        return false
      }
      setMessages((prev) => [...prev, { id: localId.current--, role: 'user', content: trimmed, citations: [], tools: [] }])
      lastSent.current = trimmed
      setState('thinking')
      setErrorKind(null) // a new question makes the last failure old news
      return true
    },
    [trySend],
  )

  const retry = useCallback(
    (text: string): boolean => {
      // Offline, the reconnect banner already says why nothing happens.
      if (!trySend({ action: 'retry', text })) return false
      lastSent.current = null // asked again: the question on screen is saved
      setState('thinking')
      setErrorKind(null)
      return true
    },
    [trySend],
  )

  const stop = useCallback(() => {
    discarding.current = trySend({ action: 'stop' })
    // Her unfinished words go (the backend saves nothing for a stopped turn, so they
    // would vanish on the next reload); what she already did stays.
    setMessages(interrupt)
    setState('idle')
  }, [trySend])

  const open = useCallback(
    (id: number | null, projectId: number | null = null) => {
      current.current = id
      project.current = projectId
      discarding.current = false
      setMessages([])
      setState('idle')
      setConversationId(id)
      setErrorKind(null) // a failure belongs to the conversation it happened in
      setAnnouncement('')
      if (trySend({ action: 'open', conversation_id: id, project_id: projectId })) {
        pendingOpens.current += 1
        openMark.current = localId.current
        setOpening(id !== null)
      } else if (refused.current) {
        // Nothing answers it until a restart: said, rather than opening for ever. The
        // reconnect that finds her memory opens it (`onopen` sends `current`).
        setOpening(false)
        if (id !== null) setErrorKind('memory_unavailable')
      } else {
        setOpening(id !== null) // the reconnect opens it
      }
    },
    [trySend],
  )

  useEffect(() => {
    let closed = false
    let retryTimer: number | null = null
    let attempt = 0

    const refs = { current, discarding, pendingOpens, openMark, ready, refused, lastSent, localId }
    const set = {
      setState,
      setLane,
      setMessages,
      setConversationId,
      setOpening,
      setErrorKind,
      setErrorDetail,
      setAnnouncement,
      setReturned,
    }

    const connect = () => {
      // Handlers check this socket (`ws`), not the ref: after a reconnect, a late event
      // from the superseded socket would otherwise tear down the live one.
      const ws = new WebSocket(WS_URL)
      socket.current = ws
      ready.current = false
      let refusedHere = false

      ws.onopen = () => {
        attempt = 0
        // A stop sent on the old socket will never be confirmed on this one.
        discarding.current = false
        // Re-attach to whatever the user is looking at; a fresh session on the backend
        // knows nothing about it. The lane opens when this is answered (`handleFrame`).
        ws.send(JSON.stringify({ action: 'open', conversation_id: current.current, project_id: project.current }))
        pendingOpens.current = 1
        openMark.current = localId.current
      }
      ws.onmessage = (event: MessageEvent<string>) => {
        try {
          const frame = JSON.parse(event.data) as Frame
          if (frame.type === 'error' && frame.kind === 'memory_unavailable') {
            // Started without her memory, where conversations live; the backend closes
            // the socket next.
            refusedHere = true
            refused.current = true
            return
          }
          handleFrame(frame, refs, set)
        } catch {
          // a malformed frame is dropped, not fatal
        }
      }
      ws.onclose = () => {
        if (closed || socket.current !== ws) return
        ready.current = false
        // Closed without a refusal, the backend is down or restarting, which says
        // nothing about chat once it is back.
        refused.current = refusedHere
        setLane(refusedHere ? 'unavailable' : 'offline')
        if (inFlight.current) setErrorKind('connection_lost')
        setState('idle')
        setMessages(interrupt)
        pendingOpens.current = 0
        const delay = refusedHere ? REFUSED_RETRY_MS : RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]
        retryTimer = window.setTimeout(() => {
          retryTimer = null
          connect()
        }, delay)
      }
      ws.onerror = () => ws.close() // funnel both paths through onclose
    }
    connect()
    retryNow.current = () => {
      if (closed || retryTimer === null) return // a socket is already on its way
      window.clearTimeout(retryTimer)
      retryTimer = null
      connect()
    }

    return () => {
      closed = true
      if (retryTimer !== null) window.clearTimeout(retryTimer)
      socket.current?.close()
      socket.current = null
    }
  }, [])

  const dismissError = useCallback(() => setErrorKind(null), [])
  const clearReturned = useCallback(() => setReturned(null), [])
  const reconnect = useCallback(() => retryNow.current(), [])

  return { messages, state, lane, conversationId, opening, errorKind, errorDetail, announcement, returned, clearReturned, send, retry, stop, open, dismissError, reconnect }
}
