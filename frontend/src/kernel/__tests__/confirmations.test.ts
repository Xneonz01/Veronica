// @vitest-environment jsdom
// Answering a permission request: an answer the server didn't act on is reported as
// not landing, and a resync keeps a request that arrived while it was fetching.

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConfirmations } from '../useConfirmations'

const lane = vi.hoisted(() => ({ handlers: {} as { onConfirmRequired?: (raw: Record<string, unknown>) => void } }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))

function respond(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: () => Promise.resolve(body) } as Response)
}

let fetchMock = vi.fn()

beforeEach(() => {
  fetchMock = vi.fn((url: string) => (String(url).endsWith('/capabilities/pending') ? respond({ pending: [] }) : respond({ resolved: true })))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

describe('answering a permission request', () => {
  it('lands when the server acted on it', async () => {
    const { result } = renderHook(() => useConfirmations())
    let landed = false
    await act(async () => {
      landed = await result.current.answer('t1', true)
    })
    expect(landed).toBe(true)
  })

  it("doesn't land on a request that had already run out", async () => {
    fetchMock.mockImplementation((url: string) =>
      String(url).endsWith('/capabilities/pending') ? respond({ pending: [] }) : respond({ resolved: false }),
    )
    const { result } = renderHook(() => useConfirmations())
    let landed = true
    await act(async () => {
      landed = await result.current.answer('t1', true)
    })
    expect(landed).toBe(false)
  })

  it('knows when a request came from something she read', async () => {
    const { result } = renderHook(() => useConfirmations())
    act(() => lane.handlers.onConfirmRequired?.({ token: 'r', capability: 'memory.save', summary: 'remember x', why: 'from_reading' }))
    act(() => lane.handlers.onConfirmRequired?.({ token: 'u', capability: 'files.write', summary: 'write y' }))
    await waitFor(() => expect(result.current.pending.map((item) => [item.token, item.fromReading])).toEqual([['r', true], ['u', false]]))
  })

  it('keeps a request that arrived while the list was being fetched', async () => {
    let finish: (value: Response) => void = () => undefined
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => (finish = resolve)))
    const { result } = renderHook(() => useConfirmations())
    act(() => lane.handlers.onConfirmRequired?.({ token: 'late', capability: 'files.write', summary: 'Write plan.md', expires_in_ms: 30000 }))
    await act(async () => finish({ ok: true, json: () => Promise.resolve({ pending: [] }) } as Response))
    await waitFor(() => expect(result.current.pending.map((item) => item.token)).toEqual(['late']))
  })
})
