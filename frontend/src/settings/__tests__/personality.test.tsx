// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { PersonalitySettings } from '../PersonalitySettings'
import { json, serve, unreachable } from './serve'

const lane = vi.hoisted(() => ({ handlers: {} as { onPersonaChanged?: (personality: string) => void } }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))

const PERSONA = {
  personality: 'Warm and direct.',
  default: 'Calm and kind.',
  max_chars: 40,
  scaffold_preview: 'You are Veronica. These rules always apply.',
}

function renderEditor() {
  const router = createMemoryRouter(
    [
      { path: '/settings', element: <PersonalitySettings /> },
      { path: '/chat', element: <p>The chat page</p> },
    ],
    { initialEntries: ['/settings?section=personality'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

const editor = async () => (await screen.findByRole('textbox', { name: 'Personality' })) as HTMLTextAreaElement

beforeEach(() => {
  stubTopLayer()
  lane.handlers = {}
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('her personality', () => {
  it('shows the saved text, nothing to save, and the rules that always apply', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    expect(await editor()).toHaveProperty('value', 'Warm and direct.')
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
    expect(screen.getByText('What always applies, whatever you write')).toBeTruthy()
    expect(screen.getByText('16 / 40')).toBeTruthy()
  })

  it('saves with Ctrl+S and says so, then shows the saved text', async () => {
    const backend = serve({
      'GET /persona': () => json(PERSONA),
      'PUT /persona': (init) => json({ ...PERSONA, personality: JSON.parse(String(init?.body)).personality }),
    })
    renderEditor()
    await userEvent.type(await editor(), ' Brief.')
    await userEvent.keyboard('{Control>}s{/Control}')
    expect(await screen.findByText("Saved. She's using it now.")).toBeTruthy()
    const put = backend.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ personality: 'Warm and direct. Brief.' })
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
  })

  it('keeps what you type while a save is on its way', async () => {
    let answer: (value: Response) => void = () => undefined
    serve({
      'GET /persona': () => json(PERSONA),
      'PUT /persona': () => new Promise<Response>((resolve) => (answer = resolve)),
    })
    renderEditor()
    const field = await editor()
    await userEvent.type(field, ' Brief.')
    await userEvent.keyboard('{Control>}s{/Control}')
    await userEvent.type(field, ' Ok.')
    await act(async () => answer(json({ ...PERSONA, personality: 'Warm and direct. Brief.' })))
    expect(await screen.findByText("Saved. She's using it now.")).toBeTruthy()
    expect(field.value).toBe('Warm and direct. Brief. Ok.')
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', false)
    expect(screen.queryByText(/saved somewhere else/)).toBeNull()
  })

  it('stops a personality over the limit before it is sent', async () => {
    const backend = serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await userEvent.type(await editor(), ' And a great deal more than fits.')
    expect(screen.getByRole('alert').textContent).toContain("That's longer than she can take")
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
    await userEvent.keyboard('{Control>}s{/Control}')
    expect(backend.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('keeps the draft and gives the reason when the backend refuses it', async () => {
    serve({
      'GET /persona': () => json(PERSONA),
      'PUT /persona': () => json({ detail: "That looks like it contains a secret, so I didn't save it." }, 422),
    })
    renderEditor()
    await userEvent.type(await editor(), ' Key 1234.')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText("That looks like it contains a secret, so I didn't save it.")).toBeTruthy()
    expect((await editor()).value).toBe('Warm and direct. Key 1234.')
  })

  it('says the backend is unreachable when a save cannot leave', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await userEvent.type(await editor(), '!')
    unreachable()
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/Can't reach Veronica right now/)).toBeTruthy()
  })

  it('follows a save made elsewhere while you are not editing', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await editor()
    act(() => lane.handlers.onPersonaChanged?.('Brisk and dry.'))
    expect((await editor()).value).toBe('Brisk and dry.')
  })

  it('warns about a save made elsewhere mid-edit, and can show it instead', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await userEvent.type(await editor(), ' Mine.')
    act(() => lane.handlers.onPersonaChanged?.('Brisk and dry.'))
    expect(screen.getByText(/was saved somewhere else while you were editing/)).toBeTruthy()
    expect((await editor()).value).toBe('Warm and direct. Mine.')
    await userEvent.click(screen.getByRole('button', { name: 'Show the new one' }))
    expect((await editor()).value).toBe('Brisk and dry.')
  })

  it('offers the original as a draft, not as a save', async () => {
    const backend = serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await userEvent.click(await screen.findByRole('button', { name: 'Start from the original' }))
    expect((await editor()).value).toBe('Calm and kind.')
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', false)
    expect(backend.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('explains what saving it empty does', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    renderEditor()
    await userEvent.clear(await editor())
    expect(screen.getByText('Saved empty, she goes back to her original personality.')).toBeTruthy()
  })

  it('asks before leaving with unsaved changes', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    const router = renderEditor()
    await userEvent.type(await editor(), ' Mine.')
    await act(() => router.navigate('/chat'))
    expect(screen.getByRole('dialog', { name: 'Leave without saving?', hidden: true })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Keep editing', hidden: true }))
    expect(router.state.location.pathname).toBe('/settings')
    expect((await editor()).value).toBe('Warm and direct. Mine.')
    await act(() => router.navigate('/chat'))
    await userEvent.click(screen.getByRole('button', { name: 'Leave', hidden: true }))
    expect(await screen.findByText('The chat page')).toBeTruthy()
  })

  it('leaves without asking when nothing changed', async () => {
    serve({ 'GET /persona': () => json(PERSONA) })
    const router = renderEditor()
    await editor()
    await act(() => router.navigate('/chat'))
    expect(await screen.findByText('The chat page')).toBeTruthy()
  })

  it('explains a failed load and tries again', async () => {
    let up = false
    serve({ 'GET /persona': () => (up ? json(PERSONA) : json({ detail: 'Her personality is unavailable.' }, 503)) })
    renderEditor()
    expect(await screen.findByText('Her personality is unavailable.')).toBeTruthy()
    up = true
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(async () => expect((await editor()).value).toBe('Warm and direct.'))
  })
})
