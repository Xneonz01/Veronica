// A stand-in backend for component tests: routes keyed by "METHOD /path" answer
// through a stubbed fetch, and anything unrouted gets the 404 a real server sends.

import { vi } from 'vitest'

export type Route = (init?: RequestInit) => Response | Promise<Response>

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function serve(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const { pathname } = new URL(String(input))
    const route = routes[`${init?.method ?? 'GET'} ${pathname}`]
    return route ? route(init) : json({ detail: 'Not Found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A backend that is not running: every request fails the way fetch does. */
export function unreachable() {
  const fetchMock = vi.fn(async () => {
    throw new TypeError('Failed to fetch')
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
