// How the chat socket's frames change the transcript on screen. Pure, so the streaming
// logic is testable without a socket (`useChat` feeds them).

import type { Caveat, ChatMessage } from './types'

type IdRef = { current: number }

/** The answer still being written, or one cut off whose actions stay on screen: a
    retry carries on in it, as its stored rows would fold together on a reload. */
function isOpenAnswer(message: ChatMessage | undefined): message is ChatMessage {
  return message?.role === 'assistant' && Boolean(message.streaming || message.cut)
}

export function appendToken(prev: ChatMessage[], text: string, ids: IdRef): ChatMessage[] {
  const last = prev[prev.length - 1]
  if (isOpenAnswer(last)) {
    return [...prev.slice(0, -1), { ...last, content: last.content + text, streaming: true, cut: false }]
  }
  return [...prev, { id: ids.current--, role: 'assistant', content: text, citations: [], tools: [], streaming: true }]
}

export function attachTool(prev: ChatMessage[], run: ChatMessage['tools'][number], ids: IdRef): ChatMessage[] {
  const last = prev[prev.length - 1]
  if (isOpenAnswer(last)) {
    return [...prev.slice(0, -1), { ...last, tools: [...last.tools, run], streaming: true, cut: false }]
  }
  // A tool can run before any prose (the model calls it, then answers), so the answer
  // that will hold the reply opens here instead of waiting for the first token.
  return [...prev, { id: ids.current--, role: 'assistant', content: '', citations: [], tools: [run], streaming: true }]
}

export function settleReply(prev: ChatMessage[], text: string, ids: IdRef, caveats: Caveat[] = []): ChatMessage[] {
  const last = prev[prev.length - 1]
  if (isOpenAnswer(last)) {
    return [...prev.slice(0, -1), { ...last, content: text, caveats, streaming: false, cut: false }]
  }
  return [...prev, { id: ids.current--, role: 'assistant', content: text, citations: [], tools: [], caveats }]
}

/** Her answer cut short (Stop, a failure, a dropped connection): the words go, and
    what she did stays, as it is stored. An answer with no actions goes entirely. */
export function interrupt(prev: ChatMessage[]): ChatMessage[] {
  const last = prev[prev.length - 1]
  if (last?.role !== 'assistant' || !last.streaming) return prev
  if (last.tools.length === 0) return prev.slice(0, -1)
  return [...prev.slice(0, -1), { ...last, content: '', streaming: false, cut: true }]
}

/** A question sent back unread (too long for her window) was never saved: it leaves
    the transcript, and its text goes back to the box (`UseChat.returned`). */
export function withoutQuestion(prev: ChatMessage[]): ChatMessage[] {
  const last = prev[prev.length - 1]
  return last?.role === 'user' && last.id < 0 ? prev.slice(0, -1) : prev
}

export function attachCitations(prev: ChatMessage[], sources: unknown): ChatMessage[] {
  if (!Array.isArray(sources) || sources.length === 0) return prev
  const last = prev[prev.length - 1]
  if (last?.role !== 'assistant') return prev
  return [...prev.slice(0, -1), { ...last, citations: sources as ChatMessage['citations'] }]
}
