// @vitest-environment jsdom
// A backend that started without her memory accepts the chat socket, says
// `memory_unavailable` and closes it: chat is paused, never open for a moment, asked
// again at the slowest pace, and open again once a restart answers an `open`.

import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { LaneState } from '../types'
import { useChat } from '../useChat'
import { installFakeSocket, socket } from './fakeSocket'

installFakeSocket()

const REFUSAL = { type: 'error', kind: 'memory_unavailable' }
const OPENED = { type: 'opened', conversation_id: null, messages: [] }

function refused(lanes: LaneState[] = []) {
  const hook = renderHook(() => {
    const chat = useChat()
    lanes.push(chat.lane)
    return chat
  })
  act(() => socket().connect())
  act(() => socket().emit(REFUSAL))
  act(() => socket().close())
  return hook
}

function sendNow(chat: { current: ReturnType<typeof useChat> }, text: string): boolean {
  let sent = true
  act(() => {
    sent = chat.current.send(text)
  })
  return sent
}

describe('a backend that started without her memory', () => {
  it('is never open, not even for the moment before it refuses (mutation: open on connect)', () => {
    const lanes: LaneState[] = []
    const { result } = refused(lanes)
    expect(lanes).not.toContain('open')
    expect(result.current.lane).toBe('unavailable')
    expect(result.current.errorKind).toBeNull() // no answer failed
  })

  it('asks again at the slowest pace', () => {
    refused()
    const first = socket()
    act(() => vi.advanceTimersByTime(14_999))
    expect(socket()).toBe(first)
    act(() => vi.advanceTimersByTime(1))
    expect(socket()).not.toBe(first)
  })

  it('sends nothing on a socket whose open is unanswered, and opens once it is', () => {
    const { result } = refused()
    act(() => vi.advanceTimersByTime(15_000))
    act(() => socket().connect())
    expect(result.current.lane).toBe('unavailable')
    expect(sendNow(result, 'Are you there?')).toBe(false)
    expect(result.current.errorKind).toBe('memory_unavailable')
    expect(socket().sent.map((payload) => payload.action)).toEqual(['open'])

    act(() => socket().emit(OPENED)) // restarted with her memory
    expect(result.current.lane).toBe('open')
    expect(result.current.errorKind).toBeNull() // the pause is over, and so is its note
    expect(sendNow(result, 'Are you there?')).toBe(true)
  })

  it('says why a conversation opened while paused is empty, instead of opening for ever', () => {
    const { result } = refused()
    act(() => result.current.open(7))
    expect(result.current.opening).toBe(false)
    expect(result.current.errorKind).toBe('memory_unavailable')

    act(() => vi.advanceTimersByTime(15_000))
    act(() => socket().connect())
    expect(socket().sent).toContainEqual({ action: 'open', conversation_id: 7, project_id: null })
  })

  it('sends a conversation asked for while the first open is on its way, and shows it', () => {
    const { result } = refused()
    act(() => vi.advanceTimersByTime(15_000))
    act(() => socket().connect())
    act(() => result.current.open(5))
    expect(socket().sent.at(-1)).toEqual({ action: 'open', conversation_id: 5, project_id: null })

    act(() => socket().emit(OPENED)) // the reconnect's own open, overtaken
    act(() => socket().emit({ type: 'opened', conversation_id: 5, messages: [] }))
    expect(result.current.conversationId).toBe(5)
  })

  it('tries again at once when told the backend is back', () => {
    const { result } = refused()
    const first = socket()
    act(() => result.current.reconnect())
    expect(socket()).not.toBe(first)

    const second = socket()
    act(() => result.current.reconnect()) // one is already on its way
    expect(socket()).toBe(second)

    act(() => socket().connect())
    act(() => socket().emit(REFUSAL))
    act(() => socket().close())
    act(() => vi.advanceTimersByTime(15_000)) // the retry's own socket this time
    const third = socket()
    act(() => result.current.reconnect())
    expect(socket()).toBe(third)
  })

  it('reads as offline, not paused, once the backend itself is down', () => {
    const { result } = refused()
    act(() => vi.advanceTimersByTime(15_000))
    act(() => socket().close()) // stopped for a restart: nothing answers
    expect(result.current.lane).toBe('offline')
    expect(sendNow(result, 'Are you there?')).toBe(false)
    expect(result.current.errorKind).toBe('offline')

    // Back without her memory: refused again, and paused again.
    act(() => vi.advanceTimersByTime(1000))
    act(() => socket().connect())
    act(() => socket().emit(REFUSAL))
    act(() => socket().close())
    expect(result.current.lane).toBe('unavailable')
  })
})
