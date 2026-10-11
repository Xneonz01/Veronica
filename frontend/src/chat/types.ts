// The shapes /ws/chat and /conversations speak, the pure fold that turns stored rows
// into display turns, and the chat hook's contract. Kept apart from the hook so it can
// be tested without a socket.

export type ChatState = 'idle' | 'thinking' | 'streaming' | 'awaiting_confirmation'

/** `open` once the backend has answered an `open` on the socket, since a backend that
 *  refuses chat accepts the socket first. `connecting` only until the first answer: a
 *  lane that dropped stays `offline` through its retries. `unavailable`: the backend is
 *  up but refuses chat, having started without her memory, where conversations live;
 *  only a restart changes that, so it is asked again at the slowest pace. */
export type LaneState = 'connecting' | 'open' | 'offline' | 'unavailable'

export interface Citation {
  n: number
  title: string
  ordinal: number
  /** The passage the model was shown. Stored with the message so the citation can
   *  still be revealed after the document is deleted. */
  text?: string
}

/** Something her answer said she did that no action of hers did: kept beside the
 *  answer, in her words, and never collapsed. */
export interface Caveat {
  family: string
  text: string
}

export interface ToolRun {
  name: string
  result: string
  ok: boolean
  code: string
}

export interface ChatMessage {
  /** Server row id once persisted; a negative local id while the turn is in flight. */
  id: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  /** Tool runs that happened while producing this reply, in order. */
  tools: ToolRun[]
  caveats?: Caveat[]
  /** True while tokens are still arriving — the caret renders from this, not from state. */
  streaming?: boolean
  /** Cut off before she finished (stopped, failed, or the connection dropped): her
   *  words went, and the actions she had already taken are all that is left. */
  cut?: boolean
}

export interface ConversationSummary {
  id: number
  title: string
  created_at: string
  updated_at: string
  message_count: number
  /** The project it is filed in; null when it is in none. */
  project_id: number | null
}

/** A stored row as the backend returns it on `open`. */
export interface WireMessage {
  id: number
  role: string
  content: string
  tool_name: string | null
  tool_ok: boolean | null
  citations: Citation[]
  caveats?: unknown
  created_at: string
}

/** Caveats as the wire or a stored row carries them, keeping only well-formed ones: what
 *  reaches the screen is a sentence the backend wrote for it, never a raw value. */
export function readCaveats(raw: unknown): Caveat[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown) => {
    if (typeof item !== 'object' || item === null) return []
    const { family, text } = item as Record<string, unknown>
    return typeof text === 'string' && text ? [{ family: String(family ?? ''), text }] : []
  })
}

/** The values a failure's words need, from its frame: a model's name, only in the
 *  shape Ollama gives one, so no other server text can ride along. */
export type ErrorDetail = Readonly<{ model?: string }>

export function readErrorDetail(frame: Record<string, unknown>): ErrorDetail {
  const { model } = frame
  return typeof model === 'string' && MODEL_NAME.test(model) ? { model } : {}
}

export const MODEL_NAME = /^[\w.:/-]{1,200}$/

/**
 * Fold the stored rows back into display turns.
 *
 * Tool rows are persisted as their own records but are shown inside the reply they
 * produced, so a reloaded conversation looks the same as it did live. Actions from
 * a turn that never answered stay with that turn, as an answer that was cut off.
 */
export function foldStoredMessages(rows: WireMessage[]): ChatMessage[] {
  const out: ChatMessage[] = []
  let pendingTools: ToolRun[] = []
  let lastToolId = 0

  // The tool row's own id is free: tool rows are never messages of their own.
  const flushCut = () => {
    if (pendingTools.length === 0) return
    out.push({ id: lastToolId, role: 'assistant', content: '', citations: [], tools: pendingTools, cut: true })
    pendingTools = []
  }

  for (const row of rows) {
    if (row.role === 'tool') {
      pendingTools.push({
        name: row.tool_name ?? '',
        result: row.content,
        ok: row.tool_ok ?? true,
        code: '',
      })
      lastToolId = row.id
      continue
    }
    if (row.role !== 'user' && row.role !== 'assistant') continue
    if (row.role === 'user') flushCut()
    out.push({
      id: row.id,
      role: row.role,
      content: row.content,
      citations: Array.isArray(row.citations) ? row.citations : [],
      caveats: readCaveats(row.caveats),
      tools: row.role === 'assistant' ? pendingTools : [],
    })
    if (row.role === 'assistant') pendingTools = []
  }
  flushCut()
  return out
}

/** What `useChat` gives the surfaces that show a conversation. */
export interface UseChat {
  messages: ChatMessage[]
  state: ChatState
  lane: LaneState
  conversationId: number | null
  /** A stored conversation was asked for and hasn't arrived (or failed) yet. Not the
   *  same as having no messages: a conversation can be empty. */
  opening: boolean
  /** The kind of the last failure (the backend's `kind`, 'offline', or
   *  'connection_lost'), for copy the UI owns. The server's words are never shown. */
  errorKind: string | null
  /** What that failure's words need (a model to pull); read only for its kind. */
  errorDetail: ErrorDetail
  /** Her last finished answer, for a screen reader to hear once, whole. */
  announcement: string
  /** A message sent back unread, to put back in the box once: the surface that does
   *  so calls `clearReturned`, or it would come back every time the page opens. */
  returned: { text: string } | null
  clearReturned: () => void
  /** False when the socket was not open: the caller must keep the user's text. */
  send: (text: string) => boolean
  /** Ask the question on screen again after a failure, without saving it twice. */
  retry: (text: string) => boolean
  stop: () => void
  open: (conversationId: number | null, projectId?: number | null) => void
  dismissError: () => void
  /** Connect now if the lane is waiting out a retry: the backend is back, as the
   *  event lane found. */
  reconnect: () => void
}
