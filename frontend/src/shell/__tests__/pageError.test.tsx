// @vitest-environment jsdom
// A page that throws is replaced inside the shell, against the app's real routes:
// the shell (and with it a live call, the chat socket and the tray) keeps running.

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, Link, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { appRoutes } from '../../lib/appRouter'

vi.mock('../AppShell', () => ({
  AppShell: () => (
    <div>
      <p>The shell</p>
      <Link to="/chat">Chat</Link>
      <Outlet />
    </div>
  ),
}))
vi.mock('../../pages/Settings', () => ({
  Settings: () => {
    throw new Error('render failed')
  },
}))
vi.mock('../../pages/Chat', () => ({ Chat: () => <p>The chat page</p> }))
vi.mock('../../pages/Call', () => ({ Call: () => null }))
vi.mock('../../pages/Companion', () => ({ Companion: () => null }))
vi.mock('../../pages/Memory', () => ({ Memory: () => null }))
vi.mock('../../pages/Voices', () => ({ Voices: () => null }))

afterEach(cleanup)

describe('a page that fails', () => {
  it('is replaced in place while the shell keeps running', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const router = createMemoryRouter(appRoutes, { initialEntries: ['/settings'] })
    render(<RouterProvider router={router} />)
    expect(screen.getByText('The shell')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'This page stopped working' })).toBeTruthy()
    expect(document.body.textContent).not.toContain('render failed')
    // Another page from the shell works again.
    await userEvent.click(screen.getByRole('link', { name: 'Chat' }))
    expect(await screen.findByText('The chat page')).toBeTruthy()
  })
})
