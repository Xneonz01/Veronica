// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage, UseChat } from '../../chat/types'
import type { NotificationContextValue } from '../../notify/context'
import { fakeNotifications, memoryNote } from '../../shell/__tests__/fixtures'
import { Providers } from '../../shell/__tests__/Providers'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { ChatSessionContext } from '../../workspace/context'
import { Chat } from '../Chat'

function message(id: number, role: 'user' | 'assistant', content: string): ChatMessage {
  return { id, role, content, citations: [], tools: [] }
}

function session(): UseChat {
  return {
    messages: [message(1, 'user', 'Forget my stamp collecting.'), message(2, 'assistant', 'Done, both are gone.')],
    state: 'idle',
    lane: 'open',
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

function page(chat: UseChat, notifications: NotificationContextValue) {
  return (
    <Providers notifications={notifications}>
      <ChatSessionContext.Provider value={chat}>
        <Chat />
      </ChatSessionContext.Provider>
    </Providers>
  )
}

beforeEach(() => {
  stubTopLayer()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(cleanup)

describe('memory receipts under the answer', () => {
  it('shows every write of the answer, and a new question starts clean', async () => {
    const chat = session()
    const { rerender } = render(page(chat, fakeNotifications()))
    // Newest first, as the notification history holds them; one landed quietly.
    const history = [
      memoryNote({ id: 'n3', action: 'removed', summary: 'User collects coins', createdAt: 3, toasted: false }),
      memoryNote({ id: 'n2', action: 'removed', summary: 'User collects stamps from Japan', createdAt: 2, toasted: true }),
      memoryNote({ id: 'n1', action: 'removed', summary: 'User collects stamps', createdAt: 1, toasted: true }),
    ]
    rerender(page(chat, fakeNotifications({ history })))

    expect(screen.getByText('User collects stamps')).toBeTruthy()
    expect(screen.getByText('User collects stamps from Japan')).toBeTruthy()
    expect(screen.queryByText('User collects coins')).toBeNull()

    await userEvent.type(screen.getByRole('textbox', { name: 'Message Veronica' }), 'Thanks')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(chat.send).toHaveBeenCalledWith('Thanks')
    expect(screen.queryByText('User collects stamps')).toBeNull()
  })
})

describe('a message sent back unread', () => {
  const box = () => screen.getByRole('textbox', { name: 'Message Veronica' })

  it('goes back in the box once (mutations: dropped on the floor; never cleared)', () => {
    const chat = session()
    const { rerender } = render(page(chat, fakeNotifications()))
    rerender(page({ ...chat, returned: { text: 'A very long paste' } }, fakeNotifications()))
    expect(box()).toHaveProperty('value', 'A very long paste')
    // Cleared once restored: the page opening again after a visit elsewhere found it
    // still there, and put it back every time.
    expect(chat.clearReturned).toHaveBeenCalledTimes(1)
  })

  it('never over what was typed since', async () => {
    const chat = session()
    const { rerender } = render(page(chat, fakeNotifications()))
    await userEvent.type(box(), 'Something new')
    rerender(page({ ...chat, returned: { text: 'A very long paste' } }, fakeNotifications()))
    expect(box()).toHaveProperty('value', 'Something new')
  })
})

describe('chat while her memory is unavailable', () => {
  it('says it is paused and points at the notice, with nothing to send', () => {
    render(page({ ...session(), lane: 'unavailable' }, fakeNotifications()))
    expect(screen.getByText("Chat is paused while her memory can't be opened. The notice above says what to do.")).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Message Veronica' }).getAttribute('placeholder')).toBe('Chat is paused')
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', true)
  })
})
