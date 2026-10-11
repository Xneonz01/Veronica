// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OFFLINE_MESSAGE } from '../../lib/api'
import { readChecks, type ComputerCheck, type Verdict } from '../../system/useDoctor'
import { ComputerChecks } from '../ComputerChecks'
import { UNIT_SPACE } from '../../shell/memorySize'
import { checkWords, summary } from '../computerCopy'
import { json, serve, unreachable } from './serve'

// The event lane, as the backend going and coming back tells the panel.
const lane = vi.hoisted(() => ({
  onConnected: () => undefined as void,
  onDisconnected: () => undefined as void,
}))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: { onConnected?: () => void; onDisconnected?: () => void }) => {
    lane.onConnected = () => handlers.onConnected?.()
    lane.onDisconnected = () => handlers.onDisconnected?.()
  },
}))

const READY = [
  { check: 'gpu', verdict: 'ok', code: 'ok', detail: { name: 'NVIDIA Example Card', total_mib: 16376, needs_mib: 14168 } },
  { check: 'ram', verdict: 'ok', code: 'ok', detail: { total_mib: 32604, needs_mib: 6327 } },
  { check: 'disk', verdict: 'ok', code: 'ok', detail: { free_mib: 781578, needs_mib: 4 } },
  { check: 'llm', verdict: 'ok', code: 'ok', detail: { model: 'qwen3.5:9b' } },
  { check: 'models', verdict: 'ok', code: 'ok', detail: { count: 5 } },
]
const NO_OLLAMA = [...READY.slice(0, 3), { check: 'llm', verdict: 'fail', code: 'unreachable', detail: {} }, READY[4]]

function row(label: string): HTMLElement {
  const found = screen.getByText(label).closest('div[class*="row"]')
  if (!(found instanceof HTMLElement)) throw new Error(`no row for ${label}`)
  return found
}

/** The words for a check, read with plain spaces (a figure keeps its unit with one that
    doesn't break, tested once below). */
function words(check: string, code: string, detail: ComputerCheck['detail'], verdict: Verdict = 'ok') {
  const { body, status } = checkWords({ check, code, verdict, detail })
  return { body: body.replaceAll(UNIT_SPACE, ' '), status }
}

/** A backend whose answers are given by hand, in any order. */
function answeredByHand() {
  const answers: ((response: Response) => void)[] = []
  const fetchMock = serve({ 'GET /diagnostics/doctor': () => new Promise<Response>((resolve) => answers.push(resolve)) })
  return { answers, fetchMock }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the words', () => {
  it('says what it found, and what is left of the memory for everything else', async () => {
    serve({ 'GET /diagnostics/doctor': () => json({ checks: READY }) })
    render(<ComputerChecks />)

    expect(await screen.findByText('NVIDIA Example Card, 16.0 GB. Measured calls used up to 13.8 GB.')).toBeTruthy()
    expect(within(row('Graphics card')).getByText('Ready')).toBeTruthy()
    expect(
      screen.getByText('31.8 GB. In measured calls, her backend and Ollama used up to 6.2 GB together, which leaves 25.6 GB for Windows and your other programs.'),
    ).toBeTruthy()
    expect(screen.getByText('763.3 GB free where her memory is kept.')).toBeTruthy()
    expect(screen.getByText('Ollama is running and has qwen3.5:9b.')).toBeTruthy()
    expect(screen.getByText('All 5 are installed.')).toBeTruthy()
    expect(screen.getByText(/^Checked at /)).toBeTruthy()
    // Opening About isn't announced; only a check someone asks for is.
    expect(screen.getByRole('status').textContent).toBe('')
  })

  it('says what falls short and what to do, never rounding a shortfall into enough', () => {
    expect(words('gpu', 'small', { name: 'Small Card', total_mib: 14100, needs_mib: 14168 }, 'warn')).toEqual({
      body: 'Small Card, 13.7 GB: less than the 13.9 GB measured calls used. Calls may be slow, or fail to start.',
      status: 'Limited',
    })
    expect(words('ram', 'small', { total_mib: 4096, needs_mib: 6327 }, 'warn').body).toBe(
      '4.0 GB: less than the 6.2 GB her backend and Ollama used together in measured calls. She may be slow.',
    )
    expect(words('disk', 'low', { free_mib: 1090, needs_mib: 1100 }, 'warn')).toEqual({
      body: 'Only 1.0 GB free where her memory is kept, and an update needs 1.1 GB there to keep a copy of her memory first. Free up some space before updating Veronica.',
      status: 'Low',
    })
    expect(words('disk', 'full', { free_mib: 0, needs_mib: 0 }, 'fail')).toMatchObject({ status: 'Full' })
    expect(words('gpu', 'absent', {}, 'fail').body).toContain('By default her speech and memory models run on one')
    expect(words('llm', 'unreachable', {}, 'fail')).toEqual({
      body: "Ollama isn't answering, and chat and calls need it. Start Ollama, or check the address in her configuration (llm.base_url), then check again.",
      status: 'Not answering',
    })
    expect(words('llm', 'model_missing', { model: 'qwen3.5:9b' }, 'fail').body).toBe(
      `Ollama doesn't have qwen3.5:9b. Install it with "ollama pull qwen3.5:9b", then check again.`,
    )
    expect(words('llm', 'no_tools', { model: 'tiny:1b' }, 'fail')).toMatchObject({ status: 'No tools' })
  })

  it('keeps a figure and its unit on one line', () => {
    const { body } = checkWords({ check: 'disk', code: 'ok', verdict: 'ok', detail: { free_mib: 2048, needs_mib: 4 } })
    expect(body).toBe(`2.0${UNIT_SPACE}GB free where her memory is kept.`)
    expect(UNIT_SPACE).toBe(String.fromCharCode(0xa0))
  })

  it('says which models are missing in the words of the start-up notice', () => {
    const optional = words('models', 'missing', { models: 'turn,rerank' }, 'warn')
    expect(optional.status).toBe('Incomplete')
    expect(optional.body).toContain('Not installed: end-of-turn detection, document ranking.')
    const needed = words('models', 'missing', { models: 'embedding' }, 'fail')
    expect(needed.status).toBe('Not installed')
    expect(needed.body).toContain('Chat answers without her memory or your documents.')
  })

  it('never shows a name that is not in the shape a driver or Ollama gives', () => {
    expect(words('gpu', 'ok', { name: '<img src=x onerror=alert(1)>', total_mib: 16376, needs_mib: 14168 }).body).toBe(
      '16.0 GB of graphics memory. Measured calls used up to 13.8 GB.',
    )
    expect(words('llm', 'model_missing', { model: 'a b' }, 'fail').body).toContain('the model her configuration names (llm.model)')
    expect(words('llm', 'ok', { model: 7 }).body).toBe('Ollama is running and has her model.')
  })

  it('has a plain line for a result from a newer backend, and never calls one that is not fine ready', () => {
    const complete = { name: 'NVIDIA Example Card', total_mib: 16376, needs_mib: 14168 }
    expect(words('gpu', 'ok', complete, 'fail').status).toBe('Not ready')
    expect(words('gpu', 'ok', complete, 'unknown').status).toBe('Unknown')
    expect(words('gpu', 'melted', {}, 'fail')).toEqual({ body: "This version of the app can't describe this result.", status: 'Not ready' })
    expect(words('ram', 'ok', { total_mib: 'lots' })).toMatchObject({ body: "This version of the app can't describe this result." })
    expect(words('disk', 'unreadable', {}, 'unknown')).toEqual({
      body: "Veronica couldn't read the free space where her memory is kept.",
      status: 'Unknown',
    })
    const read = readChecks({ checks: [{ check: 'gpu', verdict: 'splendid', code: 'ok', detail: { total_mib: 1, junk: {} } }, 'junk', { check: 1 }] })
    expect(read).toEqual([{ check: 'gpu', verdict: 'unknown', code: 'ok', detail: { total_mib: 1 } }])
    expect(() => readChecks({ checks: 'none' })).toThrow()
    expect(() => readChecks({ checks: [] })).toThrow()
  })

  it('counts, for a screen reader, what is not fine among the rows it shows', () => {
    const found = (verdicts: Verdict[], check = 'gpu') => verdicts.map((verdict) => ({ check, verdict, code: 'x', detail: {} }))
    expect(summary(found(['ok', 'ok']))).toBe('Checked. Everything is ready.')
    expect(summary(found(['warn', 'ok', 'unknown']))).toBe('Checked. 2 things need attention.')
    expect(summary([...found(['ok']), ...found(['fail'], 'mystery')])).toBe('Checked. Everything is ready.')
  })
})

describe('checking', () => {
  it('checks again on request, says it is checking, and announces what it found', async () => {
    const { answers, fetchMock } = answeredByHand()
    render(<ComputerChecks />)
    answers[0](json({ checks: READY }))
    await screen.findByText('All 5 are installed.')

    await userEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(screen.getByText('Checking…')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Check again' }).getAttribute('aria-busy')).toBe('true')
    answers[1](json({ checks: NO_OLLAMA }))

    expect(await screen.findByText('Checked. 1 thing needs attention.')).toBeTruthy()
    expect(within(row('Language model')).getByText('Not answering')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('says so when the check fails, aloud only once someone asked, and puts focus back', async () => {
    let failing = true
    serve({ 'GET /diagnostics/doctor': () => (failing ? json({ detail: 'Internal Server Error' }, 500) : json({ checks: READY })) })
    render(<ComputerChecks />)

    expect(await screen.findByText("Couldn't check this computer. Try again in a moment.")).toBeTruthy()
    expect(screen.queryByText('Internal Server Error')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull() // on opening, the version above says it

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('alert')).toBeTruthy()

    failing = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('All 5 are installed.')).toBeTruthy()
    expect(screen.getByText('Checked. Everything is ready.')).toBeTruthy()
    // The button pressed went away with the notice: focus lands on the one in its place.
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Check again' }))
  })

  it('forgets what it found once a check fails, rather than show it as current', async () => {
    const { answers } = answeredByHand()
    render(<ComputerChecks />)
    answers[0](json({ checks: READY }))
    await userEvent.click(await screen.findByRole('button', { name: 'Check again' }))
    answers[1](json({ detail: 'Internal Server Error' }, 500))
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))

    expect(screen.queryByText('All 5 are installed.')).toBeNull()
    expect(screen.getByText('Checking this computer')).toBeTruthy()
  })

  it('keeps the newest answer when an older one arrives after it, and never announces the older', async () => {
    const { answers } = answeredByHand()
    render(<ComputerChecks />)
    answers[0](json({ detail: 'Internal Server Error' }, 500))
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    act(() => lane.onConnected()) // the last check failed, so the backend coming back checks too
    expect(answers).toHaveLength(3)

    answers[2](json({ checks: READY }))
    expect(await screen.findByText('All 5 are installed.')).toBeTruthy()
    answers[1](json({ checks: NO_OLLAMA }))
    await act(() => new Promise((settle) => setTimeout(settle, 20)))
    expect(screen.getByText('All 5 are installed.')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toBe('') // the overtaken check says nothing
  })
})

describe('the backend going and coming back', () => {
  it('says the backend is out of reach, and checks again by itself when it is back', async () => {
    unreachable()
    render(<ComputerChecks />)
    expect(await screen.findByText(OFFLINE_MESSAGE)).toBeTruthy()

    const fetchMock = serve({ 'GET /diagnostics/doctor': () => json({ checks: READY }) })
    act(() => lane.onConnected())
    expect(await screen.findByText('All 5 are installed.')).toBeTruthy()
    // Back and fine, with no drop since: the next reconnect leaves it be until someone asks.
    act(() => lane.onConnected())
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('checks again after the backend went away and came back, since it may have restarted', async () => {
    const fetchMock = serve({ 'GET /diagnostics/doctor': () => json({ checks: READY }) })
    render(<ComputerChecks />)
    await screen.findByText('All 5 are installed.')

    act(() => lane.onDisconnected())
    act(() => lane.onConnected())
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })

  it('checks again when the backend came back while a check that then failed was on its way', async () => {
    const { answers, fetchMock } = answeredByHand()
    render(<ComputerChecks />)
    act(() => lane.onConnected()) // back while the first check is still on its way
    answers[0](json({ detail: 'Internal Server Error' }, 500))

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })
})
