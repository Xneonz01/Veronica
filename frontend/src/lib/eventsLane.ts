// The one connection to /ws/events, shared by everything in a window.
//
// Surfaces used to open a socket each (four in the main window, each with its own
// reconnect loop against the same backend). The lane opens with its first
// subscriber and closes with its last, reconnects with backoff, and publishes its
// status, which the shell shows as the backend's.

import { WS } from './api'

export type Frame = Record<string, unknown>

/** `connecting` only until the first answer: a lane that dropped stays `offline`
 *  through its retries, so a banner doesn't flicker on every attempt. */
export type LaneStatus = 'connecting' | 'online' | 'offline'

export interface Subscriber {
  onFrame?: (frame: Frame) => void
  /** On every (re)connect, and straight away for a subscriber joining an open lane. */
  onConnected?: () => void
  onDisconnected?: () => void
}

// Several surfaces rely on this, so a backend that is down for as long as the app
// runs must not be hammered.
const RETRY_MS = [2000, 4000, 8000, 15000] as const

export class EventsLane {
  private readonly url: string
  private readonly openSocket: (url: string) => WebSocket
  private socket: WebSocket | null = null
  private retry: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private current: LaneStatus = 'connecting'
  private readonly subscribers = new Set<Subscriber>()
  private readonly watchers = new Set<() => void>()

  constructor(url: string, openSocket: (url: string) => WebSocket = (to) => new WebSocket(to)) {
    this.url = url
    this.openSocket = openSocket
  }

  subscribe(subscriber: Subscriber): () => void {
    this.subscribers.add(subscriber)
    if (this.socket === null && this.retry === null) {
      this.connect()
    } else if (this.current === 'online') {
      queueMicrotask(() => {
        if (this.subscribers.has(subscriber)) subscriber.onConnected?.()
      })
    }
    return () => {
      this.subscribers.delete(subscriber)
      if (this.subscribers.size === 0) this.shutDown()
    }
  }

  status(): LaneStatus {
    return this.current
  }

  /** Called whenever the status changes; returns the unsubscribe. */
  watch(listener: () => void): () => void {
    this.watchers.add(listener)
    return () => {
      this.watchers.delete(listener)
    }
  }

  private connect() {
    // Every handler checks it is still the lane's socket: a late event from a
    // superseded one must not tear down its replacement or schedule a second retry.
    const ws = this.openSocket(this.url)
    this.socket = ws
    ws.onopen = () => {
      if (this.socket !== ws) return
      this.attempt = 0
      this.setStatus('online')
      this.notify((subscriber) => subscriber.onConnected?.())
    }
    ws.onmessage = (event: MessageEvent) => {
      if (this.socket !== ws) return
      const frame = parseFrame(event.data)
      if (frame) this.notify((subscriber) => subscriber.onFrame?.(frame))
    }
    ws.onclose = () => {
      if (this.socket !== ws) return
      this.socket = null
      this.setStatus('offline')
      this.notify((subscriber) => subscriber.onDisconnected?.())
      const delay = RETRY_MS[Math.min(this.attempt, RETRY_MS.length - 1)]
      this.attempt += 1
      this.retry = setTimeout(() => {
        this.retry = null
        this.connect()
      }, delay)
    }
    ws.onerror = () => ws.close() // one path for both: onclose
  }

  private shutDown() {
    if (this.retry !== null) clearTimeout(this.retry)
    this.retry = null
    const ws = this.socket
    this.socket = null // before close(), so its onclose knows it was let go
    ws?.close()
    this.attempt = 0
    this.setStatus('connecting')
  }

  private setStatus(next: LaneStatus) {
    if (this.current === next) return
    this.current = next
    for (const watcher of [...this.watchers]) watcher()
  }

  /** One subscriber's failure must not cost the others their events. */
  private notify(call: (subscriber: Subscriber) => void) {
    for (const subscriber of [...this.subscribers]) {
      try {
        call(subscriber)
      } catch {
        // a broken handler is that surface's bug; the lane carries on
      }
    }
  }
}

function parseFrame(data: unknown): Frame | null {
  try {
    const parsed: unknown = JSON.parse(String(data))
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Frame)
      : null
  } catch {
    return null // a malformed frame is dropped, not fatal
  }
}

export const eventsLane = new EventsLane(`${WS}/ws/events`)
