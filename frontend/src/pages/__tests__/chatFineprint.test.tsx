// @vitest-environment jsdom
// What the line under the composer promises follows web search: nothing leaves with it
// off, what she looks up goes to the search service with it on, and only what is true
// either way while the setting is loading.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseChat } from '../../chat/types'
import type { WebSettings } from '../../settings/useWebSettings'
import { Providers } from '../../shell/__tests__/Providers'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { ChatSessionContext } from '../../workspace/context'
import { Chat } from '../Chat'

const web = vi.hoisted(() => ({ settings: null as WebSettings | null }))
vi.mock('../../settings/useWebSettings', () => ({ useWebSettings: () => web }))

function session(lane: UseChat['lane'] = 'open'): UseChat {
  return {
    messages: [],
    state: 'idle',
    lane,
    conversationId: null,
    opening: false,
    errorKind: null,
    errorDetail: {},
    announcement: '',
    returned: null,
    clearReturned: vi.fn(),
    send: vi.fn(() => true),
    retry: vi.fn(() => true),
    stop: vi.fn(),
    open: vi.fn(),
    dismissError: vi.fn(),
    reconnect: vi.fn(),
  }
}

function fineprint(lane: UseChat['lane'] = 'open'): string {
  render(
    <Providers>
      <ChatSessionContext.Provider value={session(lane)}>
        <Chat />
      </ChatSessionContext.Provider>
    </Providers>,
  )
  const lines = screen.getAllByRole('status').map((element) => element.textContent ?? '')
  return lines.find((line) => line.startsWith('Veronica') || line.startsWith('Reconnecting')) ?? ''
}

const SETTINGS: WebSettings = { on: false, service: 'searxng', searxngUrl: 'http://127.0.0.1:8888', blockedHosts: [], keySet: false }

beforeEach(() => {
  stubTopLayer()
  Element.prototype.scrollIntoView = vi.fn()
  web.settings = null
})
afterEach(cleanup)

describe('what the chat promises', () => {
  it('says nothing leaves while web search is off', () => {
    web.settings = SETTINGS
    expect(fineprint()).toBe('Veronica runs on this computer. Nothing you say to her leaves it.')
  })

  it('says what leaves while web search is on', () => {
    // (mutation: keep the old promise whatever the setting)
    web.settings = { ...SETTINGS, on: true }
    expect(fineprint()).toBe('Veronica runs on this computer. What she looks up on the web goes to your search service.')
  })

  it('promises only what is true either way until the setting is known', () => {
    expect(fineprint()).toBe('Veronica runs on this computer.')
  })

  it('still says why when the lane is down', () => {
    web.settings = { ...SETTINGS, on: true }
    expect(fineprint('offline')).toContain('Reconnecting to Veronica.')
  })
})
