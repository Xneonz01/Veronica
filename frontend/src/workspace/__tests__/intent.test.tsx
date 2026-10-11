// @vitest-environment jsdom
// A sidebar or palette action changes what is open only once the chat page is
// reached: a page with unsaved changes that refuses the move leaves everything as
// it was, and one that lets it go does the action exactly once. And the backend
// reached again is asked for its lists, and chat for its socket, at once.

import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, Outlet, RouterProvider, useBlocker } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useWorkspace } from '../context'
import { WorkspaceProvider } from '../WorkspaceProvider'

const chat = vi.hoisted(() => ({
  messages: [],
  state: 'idle',
  lane: 'open',
  conversationId: null,
  opening: false,
  errorKind: null,
  announcement: '',
  open: vi.fn(),
  send: vi.fn(() => true),
  retry: vi.fn(),
  stop: vi.fn(),
  dismissError: vi.fn(),
  reconnect: vi.fn(),
}))
const lane = vi.hoisted(() => ({ handlers: {} as { onConnected?: () => void } }))

vi.mock('../../chat/useChat', () => ({ useChat: () => chat }))
vi.mock('../../chat/useConversations', () => ({
  useConversations: () => ({ items: [], loading: false, error: null, refresh: vi.fn(), rename: vi.fn(), remove: vi.fn(), move: vi.fn() }),
}))
vi.mock('../../chat/useProjects', () => ({
  useProjects: () => ({ items: [], loading: false, activeId: null, error: null, setActive: vi.fn(), refresh: vi.fn(), create: vi.fn(), remove: vi.fn() }),
}))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))
vi.mock('../../desktop/useDesktopEvents', () => ({ useConversationHandoff: () => undefined }))

/** A settings page holding unsaved changes, with the actions a sidebar offers. */
function Editing() {
  const workspace = useWorkspace()
  const blocker = useBlocker(true)
  return (
    <>
      <button type="button" onClick={() => workspace.openConversation(5)}>
        Open five
      </button>
      <button type="button" onClick={() => workspace.ask('Gift ideas?')}>
        Ask
      </button>
      {blocker.state === 'blocked' && (
        <>
          <button type="button" onClick={() => blocker.reset?.()}>
            Keep editing
          </button>
          <button type="button" onClick={() => blocker.proceed?.()}>
            Leave
          </button>
        </>
      )}
    </>
  )
}

function renderApp() {
  const router = createMemoryRouter(
    [
      {
        element: (
          <WorkspaceProvider>
            <Outlet />
          </WorkspaceProvider>
        ),
        children: [
          { path: '/settings', element: <Editing /> },
          { path: '/chat', element: <p>The chat page</p> },
        ],
      },
    ],
    { initialEntries: ['/settings'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('an action that leaves the page', () => {
  it('changes nothing when the move is refused', async () => {
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Open five' }))
    await userEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(chat.open).not.toHaveBeenCalled()
  })

  it('does it once the move goes ahead', async () => {
    const router = renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Open five' }))
    await userEvent.click(screen.getByRole('button', { name: 'Leave' }))
    expect(await screen.findByText('The chat page')).toBeTruthy()
    expect(chat.open).toHaveBeenCalledTimes(1)
    expect(chat.open).toHaveBeenCalledWith(5, null)
    // Spent: coming back to this entry doesn't open it again.
    await act(async () => {
      await router.navigate(-1)
      await router.navigate(1)
    })
    expect(chat.open).toHaveBeenCalledTimes(1)
  })

  it('asks a question exactly once', async () => {
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await userEvent.click(screen.getByRole('button', { name: 'Leave' }))
    await screen.findByText('The chat page')
    expect(chat.send).toHaveBeenCalledTimes(1)
    expect(chat.send).toHaveBeenCalledWith('Gift ideas?')
  })
})

describe('the backend reached again', () => {
  it('tells chat to stop waiting out its retry (mutation: reload the lists alone)', () => {
    renderApp()
    act(() => lane.handlers.onConnected?.())
    expect(chat.reconnect).toHaveBeenCalledOnce()
  })
})
