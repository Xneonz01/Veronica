// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { WebSettings } from '../WebSettings'
import { json, serve, unreachable } from './serve'

const lane = vi.hoisted(() => ({ handlers: {} as { onWebSettingsChanged?: () => void } }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))

const OFF = {
  web_search: false,
  provider: 'searxng',
  searxng_url: 'http://127.0.0.1:8888',
  blocked_hosts: [],
  brave_key_set: false,
}

beforeEach(() => {
  stubTopLayer()
  lane.handlers = {}
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('web search settings', () => {
  it('starts off and turns on at once', async () => {
    const backend = serve({
      'GET /connected': () => json(OFF),
      'PUT /connected': (init) => json({ ...OFF, ...JSON.parse(String(init?.body)) }),
    })
    render(<WebSettings />)
    const toggle = await screen.findByRole('switch', { name: 'Search the web' })
    expect(toggle.getAttribute('aria-checked')).toBe('false')

    await userEvent.click(toggle)

    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
    const [, put] = backend.mock.calls.find(([, init]) => init?.method === 'PUT') ?? []
    expect(JSON.parse(String(put?.body))).toMatchObject({ web_search: true, provider: 'searxng' })
  })

  it('says what trying SearXNG found, in words', async () => {
    serve({
      'GET /connected': () => json(OFF),
      'POST /connected/test': () => json({ ok: false, reason: 'json_off' }),
    })
    render(<WebSettings />)

    await userEvent.click(await screen.findByRole('button', { name: 'Try it' }))

    expect(await screen.findByText(/Add json under search: formats:/)).toBeTruthy()
  })

  it('takes a Brave key and never shows it again', async () => {
    const backend = serve({
      'GET /connected': () => json({ ...OFF, provider: 'brave' }),
      'PUT /connected/key': () => json({ ...OFF, provider: 'brave', brave_key_set: true }),
    })
    render(<WebSettings />)
    const field = await screen.findByLabelText('Brave Search key')

    await userEvent.type(field, 'sk-brave-FAKE-0001')
    await userEvent.click(screen.getByRole('button', { name: 'Save key' }))

    expect(await screen.findByText('A key is saved, sealed on this computer.')).toBeTruthy()
    expect(document.body.textContent).not.toContain('sk-brave-FAKE-0001')
    const [, put] = backend.mock.calls.find(([url]) => String(url).endsWith('/connected/key')) ?? []
    expect(JSON.parse(String(put?.body))).toEqual({ key: 'sk-brave-FAKE-0001' })
  })

  it('refuses an address that is not http before saving it', async () => {
    const backend = serve({ 'GET /connected': () => json(OFF) })
    render(<WebSettings />)
    const field = await screen.findByLabelText('SearXNG address')

    await userEvent.clear(field)
    await userEvent.type(field, 'file:///etc{Enter}')

    expect(await screen.findByText('Start the address with http:// or https://.')).toBeTruthy()
    expect(backend.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('follows a change made anywhere else', async () => {
    let state = OFF
    serve({ 'GET /connected': () => json(state) })
    render(<WebSettings />)
    const toggle = await screen.findByRole('switch', { name: 'Search the web' })

    state = { ...OFF, web_search: true }
    lane.handlers.onWebSettingsChanged?.()

    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'))
  })

  it('offers to try again when the settings cannot be loaded', async () => {
    unreachable()
    render(<WebSettings />)

    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
