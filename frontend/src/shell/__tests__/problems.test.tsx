// @vitest-environment jsdom
// She started without her memory: the banner says what happened and what to do, in
// the app's words, reads /health again whenever the lane reaches the backend or says
// the problems changed, and never shows a read that belongs to a backend that has gone.
// An activity log changed outside Veronica is marked as reviewed from the banner.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { json, serve } from '../../settings/__tests__/serve'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { problemCopy } from '../problemCopy'
import { ProblemBanner } from '../ProblemBanner'
import type { ToastInput } from '../toast'
import { readProblems, type Problem } from '../useProblems'

type LaneHandlers = { onConnected?: () => void; onDisconnected?: () => void; onProblemsChanged?: () => void }
const lane = vi.hoisted(() => ({ handlers: {} as LaneHandlers }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))
const toasts = vi.hoisted(() => ({ shown: [] as ToastInput[] }))
vi.mock('../toast', () => ({ useToasts: () => ({ show: (toast: ToastInput) => toasts.shown.push(toast) }) }))

const TOO_NEW = { code: 'memory_too_new', detail: { copy: 'memory.db.v5.bak' } }
const health = (problems: unknown[]) => json({ status: problems.length ? 'degraded' : 'ok', problems })

async function connect() {
  await act(async () => lane.handlers.onConnected?.())
}

beforeEach(() => {
  lane.handlers = {}
  toasts.shown = []
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the problem banner', () => {
  it('says what happened and what to do, as an alert', async () => {
    serve({ 'GET /health': () => health([TOO_NEW]) })
    render(<ProblemBanner />)
    await connect()
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Her memory is from a newer version of Veronica')
    expect(alert.textContent).toContain('rename memory.db.v5.bak to memory.db')
    expect(alert.textContent).not.toContain('memory_too_new')
  })

  it('puts what stops her before what she carries on without', async () => {
    // A data folder moved to another account locks both, voices first as they are found.
    const voices = { code: 'voices_locked', detail: { reason: 'another_account' } }
    const memory = { code: 'memory_locked', detail: { reason: 'another_account' } }
    serve({ 'GET /health': () => health([voices, memory]) })
    const { container } = render(<ProblemBanner />)
    await connect()
    const text = container.textContent ?? ''
    expect(text.indexOf('Her memory is locked')).toBeGreaterThanOrEqual(0)
    expect(text.indexOf('Her memory is locked')).toBeLessThan(text.indexOf('Her voices are locked'))
  })

  it('shows nothing when she started whole', async () => {
    serve({ 'GET /health': () => health([]) })
    const { container } = render(<ProblemBanner />)
    await connect()
    expect(container.textContent).toBe('')
  })

  it('goes when the lane drops, and comes back only if the new backend still has it', async () => {
    let problems: unknown[] = [TOO_NEW]
    serve({ 'GET /health': () => health(problems) })
    render(<ProblemBanner />)
    await connect()
    expect(screen.queryByRole('alert')).not.toBeNull()

    act(() => lane.handlers.onDisconnected?.())
    expect(screen.queryByRole('alert')).toBeNull()

    problems = [] // restarted with the problem fixed
    await connect()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('drops a read still on its way when the lane went (mutation: no numbering)', async () => {
    let answer: (response: Response) => void = () => undefined
    serve({ 'GET /health': () => new Promise<Response>((resolve) => (answer = resolve)) })
    render(<ProblemBanner />)
    await connect()
    act(() => lane.handlers.onDisconnected?.())
    await act(async () => answer(health([TOO_NEW])))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows nothing when /health answers with something unexpected', async () => {
    serve({ 'GET /health': () => json({ detail: 'Internal Server Error' }, 500) })
    const { container } = render(<ProblemBanner />)
    await connect()
    expect(container.textContent).toBe('')
  })
})

describe('reading /health', () => {
  it('keeps only problems of the expected shape, and plain values in them', () => {
    const body = {
      problems: [
        { code: 'memory_no_room', detail: { needed_mb: 12, free_mb: 3, nested: { a: 1 }, big: Infinity } },
        { code: 7 },
        'memory_damaged',
        null,
        { code: 'memory_damaged', detail: ['not', 'a', 'record'] },
      ],
    }
    expect(readProblems(body)).toEqual([
      { code: 'memory_no_room', detail: { needed_mb: 12, free_mb: 3 } },
      { code: 'memory_damaged', detail: {} },
    ])
  })

  it.each([null, [], 'degraded', { problems: 'memory_damaged' }, {}])('reads nothing from %j', (body) => {
    expect(readProblems(body)).toEqual([])
  })
})

describe('the words for each problem', () => {
  const words = (problem: Problem) => {
    const copy = problemCopy(problem)
    return `${copy.title} ${copy.body}`
  }

  it('names the models that are missing by what they are for, never by their codes', () => {
    const copy = words({ code: 'models_missing', detail: { models: 'tts,embedding,made_up,constructor' } })
    expect(copy).toContain('Not installed: speech synthesis, her memory model.')
    expect(copy).toContain('uv run python -m scripts.install_models')
    expect(copy).not.toMatch(/made_up|constructor|function/)
    expect(words({ code: 'models_missing', detail: {} })).not.toContain('Not installed')
  })

  it('says what goes without them, and how much it matters', () => {
    const memory = problemCopy({ code: 'models_missing', detail: { models: 'embedding' } })
    expect(memory).toMatchObject({ title: "One of her models isn't installed", tone: 'critical' })
    // Chat answers without it since 0.34.1, and says so under each answer.
    expect(memory.body).toContain(
      "Chat answers without her memory or your documents. Calls can't start. Install it with",
    )

    // Both are done without, less well: attention, not an alarm.
    const soft = problemCopy({ code: 'models_missing', detail: { models: 'turn,rerank' } })
    expect(soft).toMatchObject({ title: "Some of her models aren't installed", tone: 'attention' })
    expect(soft.body).toContain('Calls wait a little longer before she answers. Documents are searched a little less precisely. Install them')

    // A call that can't start doesn't also wait longer.
    expect(words({ code: 'models_missing', detail: { models: 'stt,turn' } })).not.toContain('wait a little longer')
    // A code this app has no words for is taken as one she can't do without.
    expect(problemCopy({ code: 'models_missing', detail: { models: 'rerank,vision' } }).tone).toBe('critical')
  })

  it('has its own words for every code the backend sends', () => {
    const codes = [
      'memory_too_new',
      'memory_damaged',
      'memory_unopenable',
      'memory_no_room',
      'memory_copy_failed',
      'memory_left_behind',
      'models_missing',
      'voices_damaged',
      'voices_unusable',
      'voices_locked',
      'data_recovered',
      'audit_broken',
      'memory_locked',
      'memory_not_encrypted',
    ]
    const titles = codes.map((code) => problemCopy({ code, detail: {} }).title)
    expect(new Set(titles).size).toBe(codes.length)
    expect(titles).not.toContain(problemCopy({ code: 'unknown', detail: {} }).title)
  })

  it('offers the way back only with a copy to go back to', () => {
    expect(words({ code: 'memory_too_new', detail: { copy: '' } })).not.toContain('go back')
    expect(words({ code: 'memory_too_new', detail: { copy: 'memory.db.v5.bak' } })).toContain('To go back instead')
  })

  it("moves SQLite's own files with it, or one a crash left could damage the copy", () => {
    for (const problem of [TOO_NEW, { code: 'memory_damaged', detail: {} }]) {
      expect(words(problem)).toContain('move memory.db and any file beside it whose name starts with memory.db- somewhere safe')
    }
  })

  it("names her backend, which reopening the app doesn't restart", () => {
    for (const code of ['memory_unopenable', 'memory_no_room', 'memory_copy_failed', 'unknown']) {
      expect(words({ code, detail: {} })).toMatch(/restart Veronica's backend/i)
    }
    expect(words(TOO_NEW)).toContain("stop Veronica's backend")
  })

  it('moves what was left behind whole, with her stopped, rather than start a second memory', () => {
    const left = words({ code: 'memory_left_behind', detail: {} })
    expect(left).toContain("so she didn't start a new memory without them")
    expect(left).toContain("Stop Veronica's backend, move the memory and voices folders from there into her data folder")
    expect(left).toContain('unavailable until then')
  })

  it('says how to get calls back after the voices file was set aside, and what a stuck one needs', () => {
    const aside = words({ code: 'voices_damaged', detail: {} })
    expect(aside).toContain('uv run python -m scripts.seed_default_voice')
    expect(aside).toContain('their clips are still there')
    const stuck = words({ code: 'voices_unusable', detail: {} })
    expect(stuck).toContain("can't save a new one")
    expect(stuck).not.toContain('set it aside')
  })

  it('says why her memory is locked, and what opens it, by the reason', () => {
    const account = words({ code: 'memory_locked', detail: { reason: 'another_account' } })
    expect(account).toContain('locked to another Windows account')
    expect(words({ code: 'memory_locked', detail: {} })).toBe(account) // a reason it doesn't know
    const missing = words({ code: 'memory_locked', detail: { reason: 'key_missing' } })
    expect(missing).toContain('move memory.key into it too')
    expect(words({ code: 'memory_locked', detail: { reason: 'key_damaged' } })).toContain("can't be read")
    expect(missing).toContain('unavailable until then')
  })

  it("gives both ways out when the key doesn't open the file, since the two can't be told apart", () => {
    const mismatch = words({ code: 'memory_locked', detail: { reason: 'key_mismatch' } })
    expect(mismatch).toContain("doesn't open it: it may be another memory's key, or the file may be damaged")
    expect(mismatch).toContain('memory.key.unusable')
    expect(mismatch).toContain('move memory.db and any file beside it whose name starts with memory.db- somewhere safe')
  })

  it('offers the recovery key only where one can open her data', async () => {
    const locked = { code: 'memory_locked', detail: { reason: 'another_account', recoverable: 1 } }
    expect(words(locked)).toContain('start her there. Or use the recovery key you saved.')
    expect(words({ code: 'memory_locked', detail: { reason: 'another_account' } })).not.toContain('recovery key')
    expect(words({ code: 'voices_locked', detail: { reason: 'key_damaged', recoverable: 1 } })).toContain(
      'Or use the recovery key you saved.',
    )
    serve({ 'GET /health': () => health([locked, { code: 'voices_locked', detail: { reason: 'key_missing' } }]) })
    stubTopLayer()
    render(<ProblemBanner />)
    await connect()
    expect(screen.getAllByRole('button', { name: 'Use recovery key' })).toHaveLength(1)
  })

  it('offers the recovery key once, with both her memory and voices locked', async () => {
    // Using it opens both, so a second button was the same one twice (mutation: one
    // on each notice it answers).
    const memory = { code: 'memory_locked', detail: { reason: 'another_account', recoverable: 1 } }
    const voices = { code: 'voices_locked', detail: { reason: 'another_account', recoverable: 1 } }
    serve({ 'GET /health': () => health([voices, memory]) })
    stubTopLayer()
    render(<ProblemBanner />)
    await connect()
    expect(screen.getAllByRole('button', { name: 'Use recovery key' })).toHaveLength(1)
  })

  it('says what a recovery key opened, and to restart', () => {
    const both = problemCopy({ code: 'data_recovered', detail: { memory: 1, voices: 1 } })
    expect(both).toMatchObject({ title: 'Her memory and voices open on this computer now', tone: 'attention' })
    expect(both.body).toBe("Your recovery key opened them for this Windows account. Restart Veronica's backend to use them.")
    expect(words({ code: 'data_recovered', detail: { memory: 1 } })).toContain('Her memory opens on this computer now')
    expect(words({ code: 'data_recovered', detail: { memory: 1 } })).toContain('to use it.')
    expect(words({ code: 'data_recovered', detail: { voices: 1 } })).toContain('Her voices open on this computer now')
  })

  it('says calmly that her memory is not encrypted yet, with the sizes when room was short', () => {
    const short = problemCopy({ code: 'memory_not_encrypted', detail: { needed_mb: 2048, free_mb: 100 } })
    expect(short).toMatchObject({ title: "Her memory isn't encrypted yet", tone: 'attention' })
    expect(short.body).toContain('needs 2,048 MB free beside it, and 100 MB is free')
    expect(words({ code: 'memory_not_encrypted', detail: {} })).toContain("She'll try again at the next start")
  })

  it('names a copy that could not be encrypted, only as the backend names them', () => {
    const copy = problemCopy({ code: 'memory_not_encrypted', detail: { copy: 'memory.db.v5.bak' } })
    expect(copy).toMatchObject({ title: "A copy of her memory isn't encrypted yet", tone: 'attention' })
    expect(copy.body).toContain('memory.db.v5.bak couldn')
    expect(copy.body).toContain('removes it once it is 30 days old')
    expect(words({ code: 'memory_not_encrypted', detail: { copy: '<img src=x>' } })).toContain('one of them')
  })

  it('says plainly where there is nothing to keep the key with', () => {
    const here = problemCopy({ code: 'memory_not_encrypted', detail: { reason: 'unsupported' } })
    expect(here).toMatchObject({ title: "Her memory isn't encrypted on this system", tone: 'attention' })
    expect(here.body).toContain('her memory and voice clips are stored unencrypted')
  })

  it('says why her voices are locked, that she still speaks, and what opens them', () => {
    const account = problemCopy({ code: 'voices_locked', detail: { reason: 'another_account' } })
    expect(account).toMatchObject({ title: 'Her voices are locked to another Windows account', tone: 'attention' })
    expect(words({ code: 'voices_locked', detail: {} })).toBe(words({ code: 'voices_locked', detail: { reason: 'another_account' } }))
    expect(words({ code: 'voices_locked', detail: { reason: 'key_missing' } })).toContain('move voices.key into it too')
    expect(words({ code: 'voices_locked', detail: { reason: 'key_damaged' } })).toContain("can't be read")
    expect(account.body).toContain('she speaks in her built-in voice')
  })

  it('never shows a copy name that is not one the backend makes', () => {
    for (const copy of ['<img src=x>', 'C:\\data\\memory.db.v5.bak', '../memory.db.v5.bak', '..v5.bak', 'memory.db', ' memory.db.v5.bak']) {
      expect(words({ code: 'memory_too_new', detail: { copy } })).not.toContain(copy)
    }
  })

  it('gives the sizes when the copy found too little room, and a full disk otherwise', () => {
    expect(words({ code: 'memory_no_room', detail: { needed_mb: 1234, free_mb: 0 } })).toContain('needs 1,234 MB, and 0 MB is free')
    expect(problemCopy({ code: 'memory_no_room', detail: {} }).title).toBe('The disk is full')
    expect(problemCopy({ code: 'memory_no_room', detail: { needed_mb: '12', free_mb: 3 } }).title).toBe('The disk is full')
  })

  it('says something plain for a code it has no words for, never the code', () => {
    expect(words({ code: 'gpu_melted', detail: { note: 'raw server text' } })).toBe(
      "Part of Veronica didn't start Restart Veronica's backend to try again.",
    )
  })
})

describe('an activity log changed outside Veronica', () => {
  const BROKEN = { code: 'audit_broken', detail: { row: 1234 } }

  beforeEach(() => {
    stubTopLayer()
  })

  function banner() {
    return render(<ProblemBanner />)
  }

  async function markReviewed() {
    fireEvent.click(screen.getByRole('button', { name: 'Mark as reviewed' }))
    const buttons = screen.getAllByRole('button', { name: 'Mark as reviewed' })
    expect(buttons).toHaveLength(2) // asked twice: the banner's, then the sheet's
    await act(async () => fireEvent.click(buttons[1]))
  }

  it('is said calmly, with the first entry that changed', () => {
    const copy = problemCopy(BROKEN)
    expect(copy).toMatchObject({ title: 'Her activity log was changed outside Veronica', tone: 'attention' })
    expect(copy.body).toContain('starting at entry 1,234')
    expect(problemCopy({ code: 'audit_broken', detail: { row: 0 } }).body).not.toContain('starting at')
  })

  it('is marked as reviewed once asked twice, and the problems are read again', async () => {
    let problems: unknown[] = [BROKEN]
    const fetchMock = serve({
      'GET /health': () => health(problems),
      'POST /audit/review': () => {
        problems = []
        return json({ ok: true, rows: 5, unchained: 0, broken_at: null, anchored: 1, end_moved: false })
      },
    })
    banner()
    await connect()
    expect(screen.getByRole('status').textContent).toContain('changed outside Veronica')

    await markReviewed()

    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(screen.queryByText('Her activity log was changed outside Veronica')).toBeNull()
    expect(toasts.shown.map((toast) => toast.title)).toEqual(['Marked as reviewed'])
  })

  it('keeps the question open with its reason when the review fails', async () => {
    serve({
      'GET /health': () => health([BROKEN]),
      'POST /audit/review': () => json({ code: 'internal', detail: 'Traceback (most recent call last)' }, 500),
    })
    banner()
    await connect()

    await markReviewed()

    expect(screen.getByRole('alert').textContent).toBe("Something went wrong on Veronica's side. Try again in a moment.")
    expect(screen.getAllByRole('button', { name: 'Mark as reviewed' })).toHaveLength(2)
    expect(toasts.shown).toEqual([])
  })

  it('says so when the log changed again while she checked it', async () => {
    serve({
      'GET /health': () => health([BROKEN]),
      'POST /audit/review': () => json({ ok: false, rows: 6, unchained: 0, broken_at: 6, anchored: 1, end_moved: false }),
    })
    banner()
    await connect()

    await markReviewed()

    expect(screen.getByRole('alert').textContent).toContain('The log changed again while she checked it')
  })

  it('is read again when the backend says the problems changed (mutation: only on connect)', async () => {
    let problems: unknown[] = []
    serve({ 'GET /health': () => health(problems) })
    banner()
    await connect()
    expect(screen.queryByRole('status')).toBeNull()

    problems = [BROKEN] // found by the walk after the app had read them
    await act(async () => lane.handlers.onProblemsChanged?.())

    expect(screen.getByRole('status').textContent).toContain('changed outside Veronica')
  })
})
