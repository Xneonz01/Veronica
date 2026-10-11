import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EventsLane, type LaneStatus } from '../eventsLane'

// Just enough of a WebSocket for the lane: handlers it can assign, a close that
// reports back, and test helpers for the server's side.
class FakeSocket {
  static made: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeSocket.made.push(this)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }

  accept() {
    this.onopen?.()
  }

  deliver(data: string) {
    this.onmessage?.({ data })
  }

  drop() {
    this.close()
  }
}

function lane() {
  return new EventsLane('ws://test/events', (url) => new FakeSocket(url) as unknown as WebSocket)
}

const last = () => FakeSocket.made[FakeSocket.made.length - 1]

beforeEach(() => {
  FakeSocket.made = []
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('EventsLane', () => {
  it('shares one socket between subscribers and hands each the same frames', () => {
    const events = lane()
    const first = vi.fn()
    const second = vi.fn()
    events.subscribe({ onFrame: first })
    events.subscribe({ onFrame: second })
    last().accept()
    last().deliver('{"type":"rag_updated"}')
    expect(FakeSocket.made).toHaveLength(1)
    expect(first).toHaveBeenCalledWith({ type: 'rag_updated' })
    expect(second).toHaveBeenCalledWith({ type: 'rag_updated' })
  })

  it('closes with its last subscriber and opens again with the next', () => {
    const events = lane()
    const leaveA = events.subscribe({})
    const leaveB = events.subscribe({})
    leaveA()
    expect(last().closed).toBe(false)
    leaveB()
    expect(last().closed).toBe(true)
    // Let go on purpose: no reconnect is scheduled.
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.made).toHaveLength(1)
    events.subscribe({})
    expect(FakeSocket.made).toHaveLength(2)
  })

  it('reconnects with backoff and reports its status without flickering', () => {
    const events = lane()
    const seen: LaneStatus[] = []
    events.watch(() => seen.push(events.status()))
    const connected = vi.fn()
    const disconnected = vi.fn()
    events.subscribe({ onConnected: connected, onDisconnected: disconnected })
    expect(events.status()).toBe('connecting')

    last().accept()
    last().drop()
    expect(disconnected).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(1999)
    expect(FakeSocket.made).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeSocket.made).toHaveLength(2)

    // A failed retry waits longer and stays offline rather than "connecting".
    last().drop()
    vi.advanceTimersByTime(2000)
    expect(FakeSocket.made).toHaveLength(2)
    vi.advanceTimersByTime(2000)
    expect(FakeSocket.made).toHaveLength(3)

    last().accept()
    expect(connected).toHaveBeenCalledTimes(2)
    expect(seen).toEqual(['online', 'offline', 'online'])
  })

  it('tells a subscriber that joins an open lane that it is connected', async () => {
    const events = lane()
    events.subscribe({})
    last().accept()
    const connected = vi.fn()
    events.subscribe({ onConnected: connected })
    await Promise.resolve()
    expect(connected).toHaveBeenCalledOnce()
  })

  it('drops malformed frames and keeps delivering past a broken handler', () => {
    const events = lane()
    const healthy = vi.fn()
    events.subscribe({
      onFrame: () => {
        throw new Error('surface bug')
      },
    })
    events.subscribe({ onFrame: healthy })
    last().accept()
    last().deliver('not json')
    last().deliver('[1, 2]')
    last().deliver('{"type":"voices_updated"}')
    expect(healthy).toHaveBeenCalledTimes(1)
  })

  it('ignores a superseded socket', () => {
    const events = lane()
    events.subscribe({})
    const old = last()
    old.accept()
    old.drop()
    vi.advanceTimersByTime(2000)
    last().accept()
    // A late close from the old socket must not schedule another retry.
    old.onclose?.()
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.made).toHaveLength(2)
    expect(events.status()).toBe('online')
  })
})
