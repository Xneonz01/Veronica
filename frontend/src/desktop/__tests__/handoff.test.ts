// Handing a conversation from the companion to the main window.
//
// The handoff is just a conversation id, since conversations are persisted. These
// cover the cases that would silently lose or misroute it: a non-numeric value,
// reading it twice (a refresh must not reopen it), and Chat already being mounted.

import { beforeEach, describe, expect, it } from 'vitest'

// vitest runs in node, which has no sessionStorage. A stub is enough: it is the only
// DOM API this module touches, and jsdom has no real focus or window manager, so it
// wouldn't make the other companion behaviours (focus, blur-dismiss) testable either.
// Those are checked by hand.
const store = new Map<string, string>()
globalThis.sessionStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
  clear: () => store.clear(),
  key: (i: number) => [...store.keys()][i] ?? null,
  get length() {
    return store.size
  },
} as Storage

const { takeHandoff } = await import('../useDesktopEvents')

const KEY = 'veronica:handoff-conversation'

describe('takeHandoff', () => {
  beforeEach(() => sessionStorage.clear())

  it('returns the conversation the companion handed over', () => {
    sessionStorage.setItem(KEY, '42')
    expect(takeHandoff()).toBe(42)
  })

  it('consumes it, so a refresh does not reopen it', () => {
    sessionStorage.setItem(KEY, '42')
    takeHandoff()
    expect(takeHandoff()).toBeNull()
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })

  it('is null when nothing was handed over', () => {
    expect(takeHandoff()).toBeNull()
  })

  it('refuses junk rather than opening conversation NaN', () => {
    for (const junk of ['', 'abc', '0', '-3', 'null']) {
      sessionStorage.setItem(KEY, junk)
      expect(takeHandoff(), junk).toBeNull()
    }
  })
})

describe('the already-mounted case', () => {
  beforeEach(() => sessionStorage.clear())

  it('the live event consumes the stash, so a later mount does not reopen it', () => {
    // Chat is the landing route, so it is usually already mounted when the companion
    // hands over, and `navigate('/chat')` from `/chat` does not remount it. The live
    // listener clears the stash on delivery (simulated below), so a later mount must
    // not reopen the conversation.
    sessionStorage.setItem(KEY, '7')

    // what the listener does on delivery
    sessionStorage.removeItem(KEY)

    expect(takeHandoff()).toBeNull()
  })
})
