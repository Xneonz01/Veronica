// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setTheme } from '../../lib/theme'
import { NotificationContext } from '../../notify/context'
import { Settings } from '../../pages/Settings'
import { fakeNotifications } from '../../shell/__tests__/fixtures'
import { RouteError } from '../../shell/AppError'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { AboutSettings } from '../AboutSettings'
import { GeneralSettings } from '../GeneralSettings'
import { OFFLINE_MESSAGE } from '../../lib/api'
import { json, serve, unreachable } from './serve'

// The event lane: every surface's handlers, so a test can say the backend is back.
type LaneHandlers = { onConnected?: () => void; onDisconnected?: () => void }
const lane = vi.hoisted(() => ({ handlers: new Set<LaneHandlers>() }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: LaneHandlers) => {
    lane.handlers.add(handlers)
  },
}))

const NOTES = {
  entries: [
    { version: '0.15.0', label: 'A new face', date: '2026-09-24', channel: 'dev', sections: { new: ['Light and dark themes.'], known: ['Links are not clickable yet.'] } },
    { version: '0.14.0', label: 'Presence', date: '2026-09-01', channel: 'dev', sections: { fixed: ['Calls recover after sleep.'] } },
  ],
}

function renderAt(path: string, element: ReactNode, notifications = fakeNotifications()) {
  const router = createMemoryRouter(
    [
      { path: '/settings', element },
      { path: '/', element: <p>The chat page</p> },
    ],
    { initialEntries: [path] },
  )
  render(
    <NotificationContext.Provider value={notifications}>
      <RouterProvider router={router} />
    </NotificationContext.Provider>,
  )
  return router
}

beforeEach(() => {
  stubTopLayer()
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  lane.handlers.clear()
  act(() => setTheme('system'))
})

describe('general settings', () => {
  it('switches the theme from its pictures, at once', async () => {
    renderAt('/settings', <GeneralSettings />)
    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveProperty('checked', true)
    await userEvent.click(screen.getByRole('radio', { name: 'System' }))
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('says what each alert level means and hands the choice on', async () => {
    const notifications = fakeNotifications()
    renderAt('/settings', <GeneralSettings />, notifications)
    expect(screen.getByText(/Anything else only marks the bell/)).toBeTruthy()
    await userEvent.click(screen.getByRole('radio', { name: 'Everything' }))
    expect(notifications.settings.setVerbosity).toHaveBeenCalledWith('loud')
  })

  it('turns sounds off, without forgetting them, while alerts are silent', () => {
    const notifications = fakeNotifications({
      settings: { verbosity: 'silent', sounds: true, setVerbosity: vi.fn(), setSounds: vi.fn() },
    })
    renderAt('/settings', <GeneralSettings />, notifications)
    const sounds = screen.getByRole('switch', { name: 'Sounds' })
    expect(sounds.getAttribute('aria-checked')).toBe('false')
    expect(sounds).toHaveProperty('disabled', true)
    expect(screen.getByText('Off while alerts are silent.')).toBeTruthy()
  })
})

describe('about', () => {
  it('shows the version, a warning after an unclean exit, and every release', async () => {
    serve({
      'GET /changelog': () => json(NOTES),
      'GET /diagnostics': () => json({ version: '0.15.0', label: 'A new face', channel: 'dev', previous_run: { clean_exit: false } }),
    })
    renderAt('/settings', <AboutSettings />)
    expect(await screen.findByText("The last session didn't close cleanly")).toBeTruthy()
    expect(screen.getByText((_, element) => element?.tagName === 'P' && element.textContent === 'Veronica 0.15.0')).toBeTruthy()
    const releases = document.querySelectorAll('details')
    expect(releases).toHaveLength(2)
    expect(releases[0].open).toBe(true)
    expect(releases[1].open).toBe(false)
    expect(screen.getByText('Known issues')).toBeTruthy()
    expect(screen.getByText('Links are not clickable yet.')).toBeTruthy()
  })

  it('says so when there are no patch notes, and stays quiet after a clean exit', async () => {
    serve({
      'GET /changelog': () => json({ entries: [] }),
      'GET /diagnostics': () => json({ version: '0.15.0', channel: 'dev', previous_run: { clean_exit: true } }),
    })
    renderAt('/settings', <AboutSettings />)
    expect(await screen.findByText('No patch notes yet')).toBeTruthy()
    expect(screen.queryByText("The last session didn't close cleanly")).toBeNull()
  })

  it('never shows the stock text of a failed request', async () => {
    serve({ 'GET /changelog': () => json({ detail: 'Not Found' }, 404), 'GET /diagnostics': () => json({}) })
    renderAt('/settings', <AboutSettings />)
    expect(await screen.findByText("Couldn't load the version details. Try again in a moment.")).toBeTruthy()
    expect(screen.queryByText('Not Found')).toBeNull()
  })

  it('loads again by itself once the backend is back', async () => {
    unreachable()
    renderAt('/settings', <AboutSettings />)
    expect(await screen.findAllByText(OFFLINE_MESSAGE)).toHaveLength(2) // the version and the check
    serve({ 'GET /changelog': () => json(NOTES), 'GET /diagnostics': () => json({ version: '0.15.0', channel: 'dev' }) })
    act(() => lane.handlers.forEach((handlers) => handlers.onConnected?.()))
    expect(await screen.findByText((_, element) => element?.tagName === 'P' && element.textContent === 'Veronica 0.15.0')).toBeTruthy()
  })

  it('loads again when the backend comes back from a drop, which may have been an update', async () => {
    const fetchMock = serve({ 'GET /changelog': () => json(NOTES), 'GET /diagnostics': () => json({ version: '0.15.0', channel: 'dev' }) })
    renderAt('/settings', <AboutSettings />)
    await screen.findByText((_, element) => element?.tagName === 'P' && element.textContent === 'Veronica 0.15.0')
    const loads = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/changelog')).length
    expect(loads()).toBe(1)

    act(() => lane.handlers.forEach((handlers) => handlers.onConnected?.()))
    expect(loads()).toBe(1) // back without a drop: nothing to load again
    act(() => lane.handlers.forEach((handlers) => handlers.onDisconnected?.()))
    act(() => lane.handlers.forEach((handlers) => handlers.onConnected?.()))
    await vi.waitFor(() => expect(loads()).toBe(2))
  })
})

describe('the settings page', () => {
  it('opens the part named in the address, and General for anything else', async () => {
    serve({ 'GET /changelog': () => json(NOTES), 'GET /diagnostics': () => json({ version: '0.15.0', channel: 'dev' }) })
    renderAt('/settings?section=about', <Settings />)
    expect(screen.getByRole('tab', { name: 'About' }).getAttribute('aria-selected')).toBe('true')
    cleanup()
    renderAt('/settings?section=nowhere', <Settings />)
    expect(screen.getByRole('tab', { name: 'General' }).getAttribute('aria-selected')).toBe('true')
  })

  it('moves between parts without adding to the back history', async () => {
    const router = renderAt('/settings', <Settings />)
    await userEvent.click(screen.getByRole('tab', { name: 'Desktop' }))
    expect(router.state.location.search).toBe('?section=desktop')
    expect(router.state.historyAction).toBe('REPLACE')
    expect(screen.getByRole('tabpanel').textContent).toContain('belong to the desktop app')
  })
})

describe('a failed screen', () => {
  function renderFailure(path: string, element: ReactNode) {
    const router = createMemoryRouter(
      [
        { path: '/', element: <p>The chat page</p>, errorElement: <RouteError /> },
        { path: '/broken', element, errorElement: <RouteError /> },
      ],
      { initialEntries: [path] },
    )
    render(<RouterProvider router={router} />)
    return router
  }

  function Broken(): ReactNode {
    throw new Error('render failed')
  }

  it('says an address leads nowhere, and goes back to chat', async () => {
    renderFailure('/no-such-page', <p />)
    expect(screen.getByRole('heading', { name: "There's nothing here" })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Go to chat' }))
    expect(await screen.findByText('The chat page')).toBeTruthy()
  })

  it('offers a reload when a screen throws, and never shows the error itself', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    renderFailure('/broken', <Broken />)
    expect(screen.getByRole('heading', { name: 'This screen stopped working' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()
    expect(document.body.textContent).not.toContain('render failed')
  })
})
