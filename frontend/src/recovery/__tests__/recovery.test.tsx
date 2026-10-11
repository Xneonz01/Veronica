// @vitest-environment jsdom
// Her recovery key in the app: Settings says whether one is made and still covers her
// data, makes one and shows it once (never closed before it is saved), and checks or uses
// one, with a key that can't be one caught in the field and the backend's reasons shown.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { json, serve } from '../../settings/__tests__/serve'
import type { ToastInput } from '../../shell/toast'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { keyProblem, storesNamed } from '../recoveryKey'
import { RecoveryKeyGroup } from '../RecoveryKeyGroup'
import { UseRecoveryKey } from '../UseRecoveryKey'
import { useRecoveryAlerts } from '../useRecoveryAlerts'

type LaneHandlers = {
  onConnected?: () => void
  onRecoveryUpdated?: (event: { via: string; made: string }) => void
}
const lane = vi.hoisted(() => ({ handlers: {} as LaneHandlers }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: LaneHandlers) => {
    lane.handlers = handlers
  },
}))
const toasts = vi.hoisted(() => ({ shown: [] as ToastInput[] }))
vi.mock('../../shell/toast', () => ({ useToasts: () => ({ show: (toast: ToastInput) => toasts.shown.push(toast) }) }))

const KEY = '000G-40R4-0M30-E209-185G-R38E-1W81-24GK-WYQB'
const status = (made: string | null, partial = false, unavailable: string | null = null) =>
  json({ made, partial, unavailable })

async function settle() {
  await act(async () => undefined)
}

beforeEach(() => {
  stubTopLayer()
  lane.handlers = {}
  toasts.shown = []
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('a recovery key typed back', () => {
  it('is caught early when it cannot be one, in words', () => {
    expect(keyProblem('')).toBe('Enter the recovery key you saved.')
    expect(keyProblem('uuuu')).toContain('no letter U')
    expect(keyProblem('0000-0000')).toBe("That's 8 characters. A recovery key has 36, in groups of four.")
    expect(keyProblem('0')).toContain("That's 1 character.")
    for (const typed of [KEY, KEY.toLowerCase(), KEY.replace(/-/g, ' '), ` ${KEY.replace(/-/g, '')} `]) {
      expect(keyProblem(typed)).toBeNull()
    }
  })

  it('names what it reached as her words do', () => {
    expect(storesNamed(['memory', 'voices'])).toBe('her memory and voices')
    expect(storesNamed(['voices'])).toBe('her voices')
  })
})

describe('the recovery key in Settings', () => {
  it('offers to make one when there is none', async () => {
    serve({ 'GET /recovery-key': () => status(null) })
    render(<RecoveryKeyGroup />)
    await settle()
    expect(screen.getByText('None yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Make a recovery key' })).toBeTruthy()
  })

  it('says when it was made, and offers to check it or make a new one', async () => {
    serve({ 'GET /recovery-key': () => status('2026-09-26T12:00:00+00:00') })
    render(<RecoveryKeyGroup />)
    await settle()
    // To the minute: a key made by something else the same day shows as a change.
    expect(screen.getByText(/^Made on \d{1,2} September 2026 at \d{2}:\d{2}$/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Check a key' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Make a new one' })).toBeTruthy()
  })

  it('says when it no longer covers everything', async () => {
    serve({ 'GET /recovery-key': () => status(null, true) })
    render(<RecoveryKeyGroup />)
    await settle()
    expect(screen.getByText('Out of date')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Make a new one' })).toBeTruthy()
  })

  it('says why none can be made, with no button that fails', async () => {
    serve({ 'GET /recovery-key': () => status(null, false, 'locked') })
    render(<RecoveryKeyGroup />)
    await settle()
    expect(screen.getByText('Not available')).toBeTruthy()
    expect(screen.getByText(/locked on this computer/)).toBeTruthy()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('offers to try again when it could not be read, and reads again when a key is made anywhere', async () => {
    let answer: Response = json({ detail: 'Internal Server Error' }, 500)
    const fetchMock = serve({ 'GET /recovery-key': () => answer })
    render(<RecoveryKeyGroup />)
    await settle()
    expect(screen.getByText("Couldn't read whether she has a recovery key. Try again in a moment.")).toBeTruthy()

    answer = status('2026-09-26T12:00:00+00:00')
    await act(async () => lane.handlers.onRecoveryUpdated?.({ via: 'ui', made: '' }))
    expect(screen.getByRole('button', { name: 'Check a key' })).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('making a recovery key', () => {
  async function make() {
    fireEvent.click(screen.getByRole('button', { name: 'Make a recovery key' }))
    const confirm = screen.getAllByRole('button', { name: 'Make a recovery key' })
    await act(async () => fireEvent.click(confirm[confirm.length - 1]))
  }

  it('shows it once, and keeps it open until it is saved', async () => {
    // Made, the status reads as made: the row it was made from goes, and the key must
    // not go with it (it did, before anyone could see it).
    let made: string | null = null
    serve({
      'GET /recovery-key': () => status(made),
      'POST /recovery-key': () => {
        made = '2026-09-26T12:00:00+00:00'
        return json({ key: KEY, made })
      },
    })
    render(<RecoveryKeyGroup />)
    await settle()
    await make()

    expect(screen.getByText(KEY).tagName).toBe('P')
    const done = screen.getByRole('button', { name: 'Done' }) as HTMLButtonElement
    expect(done.disabled).toBe(true)
    const sheet = screen.getByText(KEY).closest('dialog') as HTMLDialogElement
    fireEvent(sheet, new Event('cancel', { cancelable: true }))
    expect(screen.queryByText(KEY)).not.toBeNull() // Escape doesn't close it yet

    fireEvent.click(screen.getByLabelText("I've saved my recovery key"))
    fireEvent.click(done)
    expect(screen.queryByText(KEY)).toBeNull()
  })

  it('copies it, and says so when copying fails', async () => {
    const writeText = vi.fn(async () => undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    serve({ 'GET /recovery-key': () => status(null), 'POST /recovery-key': () => json({ key: KEY, made: 'x' }) })
    render(<RecoveryKeyGroup />)
    await settle()
    await make()

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy' })))
    expect(writeText).toHaveBeenCalledWith(KEY)
    expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy()

    writeText.mockRejectedValueOnce(new Error('denied'))
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copied' })))
    expect(screen.getByRole('alert').textContent).toContain("Couldn't copy it")
  })

  it('keeps the question open, with its own words for the reason, when one cannot be made', async () => {
    serve({
      'GET /recovery-key': () => status(null),
      'POST /recovery-key': () =>
        json({ code: 'recovery_locked', detail: 'Traceback (most recent call last)' }, 409),
    })
    render(<RecoveryKeyGroup />)
    await settle()
    await make()
    expect(screen.getByRole('alert').textContent).toContain("a recovery key can't be made until they open")
    expect(screen.queryByText(KEY)).toBeNull()
  })
})

describe('using or checking a recovery key', () => {
  function type(text: string) {
    fireEvent.change(screen.getByRole('textbox', { name: 'Recovery key' }), { target: { value: text } })
  }

  it('catches in the field what cannot be one, and asks the backend nothing', async () => {
    const fetchMock = serve({})
    render(<UseRecoveryKey mode="use" />)
    fireEvent.click(screen.getByRole('button', { name: 'Use recovery key' }))
    type('0000-0000')
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open her data' })))
    expect(screen.getByRole('alert').textContent).toContain("That's 8 characters")
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("says in the app's words when the key doesn't open her data", async () => {
    serve({
      'POST /recovery-key/use': () =>
        json({ code: 'recovery_key_wrong', detail: '{"detail": "raw"}' }, 422),
    })
    render(<UseRecoveryKey mode="use" />)
    fireEvent.click(screen.getByRole('button', { name: 'Use recovery key' }))
    type(KEY)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open her data' })))
    expect(screen.getByRole('alert').textContent).toBe(
      "That recovery key doesn't open her memory or voices. It may be one made before the newest.",
    )
  })

  it('says what it opened, and to restart, when it opens her data here', async () => {
    const fetchMock = serve({
      'POST /recovery-key/use': () => json({ restored: ['memory', 'voices'], opened: [], unmatched: [] }),
    })
    render(<UseRecoveryKey mode="use" />)
    fireEvent.click(screen.getByRole('button', { name: 'Use recovery key' }))
    type(KEY.toLowerCase())
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open her data' })))

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ key: KEY.toLowerCase() })
    expect(toasts.shown).toEqual([
      expect.objectContaining({ title: 'Your recovery key opened her memory and voices', body: expect.stringContaining('use them') }),
    ])
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it("sends each notice's own key, with both notices offering one", async () => {
    // Her memory's notice and her voices' each have one; the button finds its form by id,
    // and with one id for both it sent the first form, empty (mutation: a fixed id).
    const fetchMock = serve({
      'POST /recovery-key/use': () => json({ restored: ['memory', 'voices'], opened: [], unmatched: [] }),
    })
    render(
      <>
        <UseRecoveryKey mode="use" />
        <UseRecoveryKey mode="use" />
      </>,
    )
    fireEvent.click(screen.getAllByRole('button', { name: 'Use recovery key' })[1])
    // Only the open sheet's field and button are there to a screen reader.
    fireEvent.change(screen.getByRole('textbox', { name: 'Recovery key' }), { target: { value: KEY } })
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open her data' })))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ key: KEY })
  })

  it('says a checked key fits, and what it no longer covers', async () => {
    serve({ 'POST /recovery-key/use': () => json({ restored: [], opened: ['memory'], unmatched: ['voices'] }) })
    render(<UseRecoveryKey mode="check" />)
    fireEvent.click(screen.getByRole('button', { name: 'Check a key' }))
    type(KEY)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Check' })))
    const fits = screen.getByText(/That's her recovery key/)
    expect(fits.textContent).toBe(
      "That's her recovery key: it opens her memory. Her voices have a newer key it doesn't open: make a new recovery key to cover everything.",
    )
    expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy()
  })
})

describe('a new key, each time', () => {
  it('starts afresh, lands on Copy, and is cleared from the clipboard on Done', async () => {
    // A second key opened with Done already enabled, from the first key's tick
    // (mutations: keep the sheet between keys; leave the key on the clipboard).
    const writeText = vi.fn(async (_text: string) => undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    let made: string | null = null
    serve({
      'GET /recovery-key': () => status(made),
      'POST /recovery-key': () => {
        made = '2026-09-26T12:00:00+00:00'
        return json({ key: KEY, made })
      },
    })
    render(<RecoveryKeyGroup />)
    await settle()

    fireEvent.click(screen.getByRole('button', { name: 'Make a recovery key' }))
    await act(async () => fireEvent.click(screen.getAllByRole('button', { name: 'Make a recovery key' }).at(-1)!))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Copy' }))
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy' })))
    fireEvent.click(screen.getByLabelText("I've saved my recovery key"))
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(writeText.mock.calls.map(([text]) => text)).toEqual([KEY, ''])

    fireEvent.click(screen.getByRole('button', { name: 'Make a new one' }))
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Make a new key' })))
    expect((screen.getByRole('button', { name: 'Done' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('the sheet a key is typed into', () => {
  it('forgets what was typed when it closes, and stays open while it reads', async () => {
    // (mutations: keep the text for the next time; let Escape close it mid-request)
    let answer: (response: Response) => void = () => undefined
    serve({ 'POST /recovery-key/use': () => new Promise<Response>((resolve) => (answer = resolve)) })
    render(<UseRecoveryKey mode="check" />)
    fireEvent.click(screen.getByRole('button', { name: 'Check a key' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Recovery key' }), { target: { value: KEY } })
    fireEvent.click(screen.getByRole('button', { name: 'Check' }))
    const sheet = screen.getByRole('textbox', { name: 'Recovery key' }).closest('dialog') as HTMLDialogElement
    fireEvent(sheet, new Event('cancel', { cancelable: true }))
    expect(sheet.hasAttribute('open')).toBe(true)
    await act(async () => answer(json({ code: 'recovery_key_wrong', detail: 'x' }, 422)))

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Check a key' }))
    expect((screen.getByRole('textbox', { name: 'Recovery key' }) as HTMLInputElement).value).toBe('')
  })
})

describe('a key made by another program', () => {
  function Alerts() {
    useRecoveryAlerts()
    return null
  }

  it('is said in every window, with the time; one made in the app is not', () => {
    // (mutation: no word of it)
    render(<Alerts />)
    act(() => lane.handlers.onRecoveryUpdated?.({ via: 'ui', made: '2026-09-26T12:00:00+00:00' }))
    expect(toasts.shown).toEqual([])
    act(() => lane.handlers.onRecoveryUpdated?.({ via: 'api', made: '2026-09-26T12:00:00+00:00' }))
    expect(toasts.shown).toEqual([
      expect.objectContaining({ tone: 'critical', title: 'Another program made a new recovery key' }),
    ])
    expect(toasts.shown[0].body).toMatch(/made at \d{2}:\d{2} outside this app/)
  })
})
