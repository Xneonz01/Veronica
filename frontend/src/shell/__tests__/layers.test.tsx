// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Confirmation } from '../../kernel/useConfirmations'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { CommandPalette } from '../CommandPalette'
import type { PaletteItem } from '../palette'
import { PermissionPrompts } from '../PermissionPrompts'
import { useToasts } from '../toast'
import { ToastProvider } from '../Toasts'
import { fakeNotifications, memoryNote } from './fixtures'
import { Providers } from './Providers'

const confirmations = vi.hoisted(() => ({ pending: [] as Confirmation[], answer: vi.fn(async (_token: string, _approved: boolean) => true) }))
vi.mock('../../kernel/useConfirmations', () => ({ useConfirmations: () => confirmations }))

beforeEach(() => {
  stubTopLayer()
  // jsdom has no layout, so no scrolling either; the palette keeps its highlight in view.
  Element.prototype.scrollIntoView = vi.fn()
  confirmations.answer.mockReset().mockResolvedValue(true)
  confirmations.pending = [
    {
      token: 't1',
      capability: 'files.write',
      summary: 'Write 2 lines to notes/todo.md',
      risk: 'guarded',
      expiresInMs: 45000,
      expiresAt: Date.now() + 45000,
      fromReading: false,
    },
  ]
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('permission prompt', () => {
  function renderPrompts(toast = vi.fn()) {
    return render(
      <Providers toast={toast}>
        <PermissionPrompts />
      </Providers>,
    )
  }

  it('names the request in words and answers both ways', async () => {
    renderPrompts()
    const prompt = screen.getByRole('alertdialog', { name: 'Can I go ahead?' })
    expect(prompt.textContent).toContain('Write 2 lines to notes/todo.md')
    expect(prompt.textContent).toContain('Write a file')
    expect(prompt.textContent).not.toContain('files.write')
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }))
    expect(confirmations.answer).toHaveBeenCalledWith('t1', true)
    await userEvent.click(screen.getByRole('button', { name: "Don't allow" }))
    expect(confirmations.answer).toHaveBeenCalledWith('t1', false)
  })

  it('says when the words came from something she read', () => {
    confirmations.pending = [{ ...confirmations.pending[0], capability: 'memory.save', fromReading: true }]
    renderPrompts()
    const prompt = screen.getByRole('alertdialog', { name: 'Can I go ahead?' })
    expect(prompt.textContent).toContain('From something I read')
    expect(prompt.getAttribute('aria-describedby')).toBeTruthy()
    const described = document.getElementById(prompt.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toContain('The words for this came from something I read, not from you.')
  })

  it('announces where the words came from when the card cannot take the keyboard', () => {
    confirmations.pending = [{ ...confirmations.pending[0], capability: 'memory.save', fromReading: true }]
    const composer = document.createElement('textarea')
    document.body.append(composer)
    composer.focus()
    renderPrompts()
    expect(screen.getByRole('alert').textContent).toContain('The words for this came from something I read, not from you.')
    composer.remove()
  })

  it('leaves the keyboard alone while something is being typed', () => {
    const composer = document.createElement('textarea')
    document.body.append(composer)
    composer.focus()
    renderPrompts()
    expect(document.activeElement).toBe(composer)
    composer.remove()
  })

  it('takes the keyboard when nothing is being typed, and Escape means no', () => {
    renderPrompts()
    const prompt = screen.getByRole('alertdialog')
    expect(document.activeElement).toBe(prompt)
    fireEvent.keyDown(prompt, { key: 'Escape' })
    expect(confirmations.answer).toHaveBeenCalledWith('t1', false)
  })

  it('says so when an Allow never reached her, and stays quiet about a refusal', async () => {
    confirmations.answer.mockResolvedValue(false)
    const toast = vi.fn()
    renderPrompts(toast)
    await userEvent.click(screen.getByRole('button', { name: "Don't allow" }))
    expect(toast).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }))
    await vi.waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ tone: 'critical', title: "Your OK didn't reach her" })))
  })
})

describe('toasts', () => {
  function Trigger() {
    const toasts = useToasts()
    return (
      <button type="button" onClick={() => toasts.show({ tone: 'critical', title: "Couldn't send that" })}>
        fail
      </button>
    )
  }

  it('says what she remembered in her own words, with Undo', async () => {
    const notifications = fakeNotifications({ toasts: [memoryNote()] })
    render(
      <Providers notifications={notifications}>
        <ToastProvider>{null}</ToastProvider>
      </Providers>,
    )
    expect(screen.getByText("I'll remember that")).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(notifications.undo).toHaveBeenCalledWith(memoryNote())
  })

  it('goes on its own, but not while it is pointed at', () => {
    vi.useFakeTimers()
    render(
      <Providers>
        <ToastProvider>
          <Trigger />
        </ToastProvider>
      </Providers>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'fail' }))
    const toast = screen.getByRole('alert')
    fireEvent.pointerEnter(toast)
    act(() => vi.advanceTimersByTime(30_000))
    expect(screen.queryByRole('alert')).not.toBeNull()
    fireEvent.pointerLeave(toast)
    act(() => vi.advanceTimersByTime(10_000))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('command palette', () => {
  const run = vi.fn()
  const items = (): PaletteItem[] => [
    { id: 'memory', group: 'Go to', label: 'Memory', icon: null, run: () => run('memory') },
    { id: 'voices', group: 'Go to', label: 'Voices', icon: null, run: () => run('voices') },
  ]

  it('moves with the arrow keys and runs the highlighted item on Enter', async () => {
    const close = vi.fn()
    render(<CommandPalette open onClose={close} items={items} />)
    const input = screen.getByRole('combobox', { name: 'Search or ask' })
    expect(document.activeElement).toBe(input)
    await userEvent.keyboard('{ArrowDown}{Enter}')
    expect(run).toHaveBeenCalledWith('voices')
    expect(close).toHaveBeenCalled()
  })

  it('narrows as you type and tells you when nothing matches', async () => {
    render(<CommandPalette open onClose={() => undefined} items={items} />)
    await userEvent.type(screen.getByRole('combobox'), 'zzz')
    expect(screen.getByText('Nothing matches “zzz”.')).toBeTruthy()
  })
})
