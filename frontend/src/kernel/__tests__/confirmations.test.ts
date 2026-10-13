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
    await waitFor(() => expect(result.current.pending.map((item) => [item.token, item.why])).toEqual([['r', 'from_reading'], ['u', null]]))
  })

  it('knows when a request came after reading the web', async () => {
    const { result } = renderHook(() => useConfirmations())
    act(() => lane.handlers.onConfirmRequired?.({ token: 'w', capability: 'memory.save', summary: 'remember x', why: 'after_web' }))
    await waitFor(() => expect(result.current.pending.map((item) => item.why)).toEqual(['after_web']))
  })

  it('reads where a request goes and what it carries, and no reason it does not know', async () => {
    const { result } = renderHook(() => useConfirmations())
    act(() =>
      lane.handlers.onConfirmRequired?.({
        token: 'd',
        capability: 'web.read',
        summary: 'read a page on xn--pple-43d.com',
        why: 'something_new',
        display: {
          target: 'xn--pple-43d.com',
          target_read_as: 'аpple.com',
          details: [
            { field: 'url', value: 'https://xn--pple-43d.com/', more: 5, masked: 12 },
            { field: 3, value: 'x' },
            'junk',
            { field: 'n', value: 'x', more: -2, masked: -1 },
          ],
        },
      }),
    )
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const [item] = result.current.pending
    expect(item.why).toBeNull()
    expect([item.target, item.targetReadAs]).toEqual(['xn--pple-43d.com', 'аpple.com'])
    expect(item.details).toEqual([
      { field: 'url', value: 'https://xn--pple-43d.com/', more: 5, masked: 12 },
      { field: 'n', value: 'x', more: 0, masked: 0 },
    ])
  })

  it('fetches the whole of a request for its prompt, and nothing when it is gone', async () => {
    fetchMock.mockImplementation((url: string) =>
      String(url).endsWith('/capabilities/pending/t1')
        ? respond({ values: { content: 'all of it', n: 3 } })
        : respond({ values: { content: 'gone' } }, false),
    )
    const { result } = renderHook(() => useConfirmations())
    let values: Record<string, string> | null = null
    await act(async () => {
      values = await result.current.fullValues('t1')
    })
    expect(values).toEqual({ content: 'all of it' })
    await act(async () => {
      values = await result.current.fullValues('t2')
    })
    expect(values).toBeNull()
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
