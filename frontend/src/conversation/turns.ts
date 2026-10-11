// A conversation as turns: each question with the answer that followed it. An
// answer with no question before it (she spoke first) is a turn of its own.

import type { ChatMessage } from '../chat/types'

export type Turn = { key: string; question: ChatMessage | null; answer: ChatMessage | null }

export function toTurns(messages: readonly ChatMessage[]): Turn[] {
  const turns: Turn[] = []
  for (const message of messages) {
    const last = turns[turns.length - 1]
    if (message.role === 'user') turns.push({ key: `q${message.id}`, question: message, answer: null })
    else if (last && last.answer === null) turns[turns.length - 1] = { ...last, answer: message }
    else turns.push({ key: `a${message.id}`, question: null, answer: message })
  }
  return turns
}

/** A question sent from this window and not yet saved carries a negative local id. */
export function isJustSent(turn: Turn | undefined): boolean {
  return turn?.question != null && turn.question.id < 0
}
