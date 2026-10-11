// @vitest-environment jsdom
// The chat socket's edges: a conversation that can't be opened, one with nothing
// in it, frames from a conversation just left, Stop racing the end of an answer,
// and a message written while the connection is down. A backend that refuses chat
// is `refused.test.ts`.

import { act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { connected, installFakeSocket, socket } from './fakeSocket'

const row = (id: number, role: string, content: string) => ({
  id,
  role,
  content,
  tool_name: null,
  tool_ok: null,
  citations: [],
  created_at: '2026-01-01T00:00:00Z',
})

installFakeSocket()

describe('the chat socket', () => {
  it('stops opening, and says why, when a conversation is gone', () => {
    const { result } = connected()
    act(() => result.current.open(77))
    expect(result.current.opening).toBe(true)
    act(() => socket().emit({ type: 'error', kind: 'bad_conversation', message: 'Conversation 77 not found' }))
    expect(result.current.opening).toBe(false)
    expect(result.current.errorKind).toBe('bad_conversation')
    expect(result.current.conversationId).toBeNull() // what is written next starts a new one
  })

  it('finishes opening a conversation with nothing in it', () => {
    const { result } = connected()
    act(() => result.current.open(5))
    act(() => socket().emit({ type: 'opened', conversation_id: 5, messages: [] }))
    expect(result.current.opening).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(result.current.conversationId).toBe(5)
  })

  it('is never opening for a new conversation', () => {
    const { result } = connected()
    act(() => result.current.open(null))
    expect(result.current.opening).toBe(false)
  })

  it('draws nothing from the conversation it just left', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Tell me a story.')
    })
    act(() => socket().emit({ type: 'token', text: 'Once' }))
    act(() => result.current.open(5))
    // Still on their way when the open was sent: the turn it cancelled.
    act(() => socket().emit({ type: 'token', text: ' upon' }))
    act(() => socket().emit({ type: 'conversation', conversation_id: 3 }))
    act(() => socket().emit({ type: 'error', kind: 'turn_failed' }))
    expect(result.current.messages).toEqual([])
    act(() =>
      socket().emit({ type: 'opened', conversation_id: 5, messages: [row(40, 'user', 'Hi'), row(41, 'assistant', 'Hello.')] }),
    )
    expect(result.current.messages.map((message) => message.content)).toEqual(['Hi', 'Hello.'])
    expect(result.current.conversationId).toBe(5)
    expect(result.current.errorKind).toBeNull()
  })

  it('answers only the newest of several opens', () => {
    const { result } = connected()
    act(() => result.current.open(4))
    act(() => result.current.open(5))
    act(() => socket().emit({ type: 'opened', conversation_id: 4, messages: [row(1, 'user', 'From four')] }))
    expect(result.current.opening).toBe(true)
    act(() => socket().emit({ type: 'opened', conversation_id: 5, messages: [row(2, 'user', 'From five')] }))
    expect(result.current.messages.map((message) => message.content)).toEqual(['From five'])
  })

  it('keeps a question asked while a new conversation opens', () => {
    // The palette's "Ask" opens a new chat and sends straight away.
    const { result } = connected()
    act(() => {
      result.current.open(null)
      result.current.send('Gift ideas?')
    })
    act(() => socket().emit({ type: 'opened', conversation_id: null, messages: [] }))
    act(() => socket().emit({ type: 'token', text: 'A good book.' }))
    expect(result.current.messages.map((message) => message.content)).toEqual(['Gift ideas?', 'A good book.'])
  })

  it('drops words still on their way after Stop, until the backend confirms it', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Tell me a long story.')
    })
    act(() => socket().emit({ type: 'state', state: 'thinking' }))
    act(() => socket().emit({ type: 'token', text: 'Once upon ' }))
    act(() => result.current.stop())
    expect(result.current.messages.map((message) => message.role)).toEqual(['user'])

    act(() => socket().emit({ type: 'token', text: 'a time' }))
    act(() => socket().emit({ type: 'state', state: 'idle' }))
    expect(result.current.messages.map((message) => message.role)).toEqual(['user'])

    act(() => socket().emit({ type: 'stopped' }))
    act(() => {
      result.current.send('Something shorter, then.')
    })
    act(() => socket().emit({ type: 'token', text: 'Sure.' }))
    expect(result.current.messages.at(-1)).toMatchObject({ role: 'assistant', content: 'Sure.' })
  })

  it('keeps an answer that finished as Stop was pressed', () => {
    // `reply` is only sent once the answer is saved: a reload would show it.
    const { result } = connected()
    act(() => {
      result.current.send('Quick one?')
    })
    act(() => socket().emit({ type: 'token', text: 'Done' }))
    act(() => result.current.stop())
    act(() => socket().emit({ type: 'reply', text: 'Done.' }))
    expect(result.current.messages.map((message) => message.content)).toEqual(['Quick one?', 'Done.'])
  })

  it('reads out what the answer said she did and did not, with the answer', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Forget the gym.')
    })
    const caveat = { family: 'memory', text: "My memory didn't actually change just now. Ask me again if you want it to." }
    act(() => socket().emit({ type: 'reply', text: "I've forgotten that.", caveats: [caveat] }))
    expect(result.current.announcement).toBe("I've forgotten that. My memory didn't actually change just now. Ask me again if you want it to.")
    expect(result.current.messages.at(-1)?.caveats).toEqual([caveat])
  })

  it('says so when the connection drops mid-answer, and keeps what she did', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Write the plan down.')
    })
    act(() => socket().emit({ type: 'tool', name: 'files_write', result: 'Wrote plan.md.', ok: true }))
    act(() => socket().emit({ type: 'token', text: 'I wrote' }))
    act(() => socket().close())
    expect(result.current.errorKind).toBe('connection_lost')
    expect(result.current.messages.at(-1)).toMatchObject({ role: 'assistant', content: '', cut: true })
  })

  it('asks again without a second copy of the question', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Gift ideas?')
    })
    act(() => socket().emit({ type: 'error', kind: 'llm_unreachable' }))
    act(() => {
      result.current.retry('Gift ideas?')
    })
    expect(socket().sent.at(-1)).toEqual({ action: 'retry', text: 'Gift ideas?' })
    expect(result.current.messages.filter((message) => message.role === 'user')).toHaveLength(1)
    expect(result.current.state).toBe('thinking')
    expect(result.current.errorKind).toBeNull()
  })

  it('keeps a message it could not send, and asks for the conversation again on reconnect', () => {
    const { result } = connected()
    act(() => result.current.open(9))
    act(() => socket().close())
    expect(result.current.lane).toBe('offline')
    let sent = true
    act(() => {
      sent = result.current.send('Are you there?')
    })
    expect(sent).toBe(false)
    expect(result.current.errorKind).toBe('offline')
    expect(result.current.messages).toEqual([])

    act(() => vi.advanceTimersByTime(1000))
    act(() => socket().connect())
    expect(socket().sent).toContainEqual({ action: 'open', conversation_id: 9, project_id: null })
    expect(result.current.lane).toBe('offline') // open once the backend answers it
    act(() => socket().emit({ type: 'opened', conversation_id: 9, messages: [] }))
    expect(result.current.lane).toBe('open')
  })

  it('keeps the model a failure names, for its words (bug 30)', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Gift ideas?')
    })
    act(() => socket().emit({ type: 'error', kind: 'llm_model_missing', message: 'server words', model: 'qwen3.5:9b' }))
    expect(result.current.errorKind).toBe('llm_model_missing')
    expect(result.current.errorDetail).toEqual({ model: 'qwen3.5:9b' })
  })

  it.each(['message_too_long', 'context_too_small'])('hands back a question %s, never saved, to the box', (kind) => {
    // (mutation: leave the question on screen and the text nowhere)
    const { result } = connected()
    act(() => {
      result.current.send('A very long paste')
    })
    act(() => socket().emit({ type: 'error', kind }))
    expect(result.current.messages).toEqual([])
    expect(result.current.returned).toEqual({ text: 'A very long paste' })
  })

  it('forgets a returned message once a surface has put it back', () => {
    const { result } = connected()
    act(() => {
      result.current.send('A very long paste')
    })
    act(() => socket().emit({ type: 'error', kind: 'message_too_long' }))
    act(() => result.current.clearReturned())
    expect(result.current.returned).toBeNull()
  })

  it('keeps a message past the socket limit in the box, unsent', () => {
    // Over 16 MiB the socket closed and the message was lost (mutation: send it).
    const { result } = connected()
    let sent = true
    act(() => {
      sent = result.current.send('x'.repeat(1_000_001))
    })
    expect(sent).toBe(false)
    expect(result.current.errorKind).toBe('message_too_long')
    expect(socket().sent.filter((frame) => frame.action === 'message')).toEqual([])
  })

  it('leaves a question asked again where it is: it was saved the first time', () => {
    const { result } = connected()
    act(() => {
      result.current.send('Gift ideas?')
    })
    act(() => socket().emit({ type: 'error', kind: 'turn_failed' }))
    act(() => {
      result.current.retry('Gift ideas?')
    })
    act(() => socket().emit({ type: 'error', kind: 'message_too_long' }))
    expect(result.current.messages.map((message) => message.content)).toEqual(['Gift ideas?'])
    expect(result.current.returned).toBeNull()
  })

  it('stays offline through its retries rather than flickering to connecting at each', () => {
    const { result } = connected()
    act(() => socket().close())
    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.lane).toBe('offline')
  })
})
