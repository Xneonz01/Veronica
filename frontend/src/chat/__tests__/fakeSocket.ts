// A stand-in for the browser's WebSocket that the chat tests drive by hand: opened,
// sent frames, and closed when the test says so. Installed by `installFakeSocket()`.

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { useChat } from '../useChat'

export class FakeSocket {
  static OPEN = 1
  static last: FakeSocket | null = null
  readyState = 0
  sent: Record<string, unknown>[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor() {
    FakeSocket.last = this
  }

  connect() {
    this.readyState = 1
    this.onopen?.()
  }

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }

  emit(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }
}

/** The newest socket the hook opened. */
export function socket(): FakeSocket {
  if (!FakeSocket.last) throw new Error('no socket yet')
  return FakeSocket.last
}

/** A connected socket whose first `open` (sent on every connect) has its answer. */
export function connected() {
  const hook = renderHook(() => useChat())
  act(() => socket().connect())
  act(() => socket().emit({ type: 'opened', conversation_id: null, messages: [] }))
  return hook
}

/** Fake timers and this socket for every test in the file. */
export function installFakeSocket() {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeSocket.last = null
    vi.stubGlobal('WebSocket', FakeSocket)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })
}
