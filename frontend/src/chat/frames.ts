// What each frame on /ws/chat does to the chat on screen. The socket and its reconnects
// are `useChat`'s; this is only what a frame means once it has arrived.

import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { appendToken, attachCitations, attachTool, interrupt, settleReply, withoutQuestion } from './transcript'
import {
  foldStoredMessages,
  readCaveats,
  readErrorDetail,
  type ChatMessage,
  type ChatState,
  type ErrorDetail,
  type LaneState,
  type WireMessage,
} from './types'

export interface Frame {
  type?: unknown
  [key: string]: unknown
}

// Refused before anything was saved: the question goes back where it was written.
const SENT_BACK = new Set(['message_too_long', 'context_too_small'])

type Setter<T> = Dispatch<SetStateAction<T>>

/** The hook's refs a frame reads or moves on; see `useChat` for what each one holds. */
export interface ChatRefs {
  current: MutableRefObject<number | null>
  discarding: MutableRefObject<boolean>
  pendingOpens: MutableRefObject<number>
  openMark: MutableRefObject<number>
  ready: MutableRefObject<boolean>
  refused: MutableRefObject<boolean>
  lastSent: MutableRefObject<string | null>
  localId: MutableRefObject<number>
}

/** The hook's state a frame can change. */
export interface ChatSetters {
  setState: Setter<ChatState>
  setLane: Setter<LaneState>
  setMessages: Setter<ChatMessage[]>
  setConversationId: Setter<number | null>
  setOpening: Setter<boolean>
  setErrorKind: Setter<string | null>
  setErrorDetail: Setter<ErrorDetail>
  setAnnouncement: Setter<string>
  setReturned: Setter<{ text: string } | null>
}

export function handleFrame(frame: Frame, refs: ChatRefs, set: ChatSetters): void {
  const type = String(frame.type ?? '')
  if (refs.pendingOpens.current > 0) {
    const answersOpen = type === 'opened' || (type === 'error' && frame.kind === 'bad_conversation')
    if (!answersOpen) return
    if (!refs.ready.current) {
      // The socket's first answer: the backend takes chat, and a pause is over.
      refs.ready.current = true
      refs.refused.current = false
      set.setLane('open')
      set.setErrorKind((kind) => (kind === 'memory_unavailable' ? null : kind))
    }
    refs.pendingOpens.current -= 1
    if (refs.pendingOpens.current > 0) return // an open a later one overtook
  }
  if (type === 'stopped') {
    refs.discarding.current = false
    return
  }
  // After Stop, only an answer's end still counts: `reply` is sent once the answer is
  // saved, so one that finished as Stop was pressed stays, as a reload shows it.
  if (refs.discarding.current && type !== 'reply' && type !== 'citations') return
  switch (type) {
    case 'state':
      set.setState(String(frame.state ?? 'idle') as ChatState)
      break
    case 'token':
      set.setState('streaming')
      set.setMessages((prev) => appendToken(prev, String(frame.text ?? ''), refs.localId))
      break
    case 'tool':
      set.setMessages((prev) =>
        attachTool(
          prev,
          { name: String(frame.name ?? ''), result: String(frame.result ?? ''), ok: frame.ok !== false, code: String(frame.code ?? '') },
          refs.localId,
        ),
      )
      break
    case 'reply': {
      // The authoritative text (invented citation markers already removed), so it
      // replaces the streamed preview rather than appending to it.
      const text = String(frame.text ?? '')
      const caveats = readCaveats(frame.caveats)
      set.setMessages((prev) => settleReply(prev, text, refs.localId, caveats))
      set.setAnnouncement([text, ...caveats.map((caveat) => caveat.text)].join(' '))
      break
    }
    case 'citations':
      set.setMessages((prev) => attachCitations(prev, frame.sources))
      break
    case 'conversation':
      refs.current.current = Number(frame.conversation_id)
      set.setConversationId(Number(frame.conversation_id))
      break
    case 'opened': {
      const id = typeof frame.conversation_id === 'number' ? frame.conversation_id : null
      const stored = foldStoredMessages((frame.messages ?? []) as WireMessage[])
      const mark = refs.openMark.current
      refs.current.current = id
      set.setConversationId(id)
      set.setMessages((prev) => [...stored, ...prev.filter((message) => message.id <= mark)])
      set.setOpening(false)
      break
    }
    case 'error':
      set.setOpening(false)
      set.setErrorKind(String(frame.kind ?? 'turn_failed'))
      set.setErrorDetail(readErrorDetail(frame))
      if (frame.kind === 'bad_conversation') {
        // The backend has let go of it too: what is written next starts a new one.
        refs.current.current = null
        set.setConversationId(null)
      }
      if (SENT_BACK.has(String(frame.kind)) && refs.lastSent.current !== null) {
        set.setReturned({ text: refs.lastSent.current })
        set.setMessages(withoutQuestion)
      }
      set.setMessages(interrupt)
      break
  }
}
