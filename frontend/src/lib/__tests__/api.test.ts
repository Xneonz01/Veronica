import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch, failureMessage, messageFor, OFFLINE_MESSAGE } from '../api'

function reply(body: unknown, status: number, statusText = ''): Response {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, statusText })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('what a failed response may say', () => {
  const fallback = 'Something general.'

  it('passes on curated copy from the backend', async () => {
    expect(await failureMessage(reply({ detail: 'That conversation is no longer here.' }, 404), fallback)).toBe(
      'That conversation is no longer here.',
    )
  })

  it('maps a known code to its copy', async () => {
    expect(await failureMessage(reply({ code: 'untrusted_origin', detail: 'origin x' }, 403), fallback)).toBe(
      'That request was blocked for security.',
    )
  })

  it('never shows a status phrase the server filled in by itself', async () => {
    for (const [status, phrase] of [
      [404, 'Not Found'],
      [405, 'Method Not Allowed'],
      [500, 'Internal Server Error'],
    ] as const) {
      expect(await failureMessage(reply({ detail: phrase }, status), fallback)).toBe(fallback)
    }
    expect(await failureMessage(reply({ detail: "I'm a teapot" }, 418, "I'm a teapot"), fallback)).toBe(fallback)
  })

  it('never renders a validation error list, an empty detail or a page that is not JSON', async () => {
    const validation = { detail: [{ loc: ['body', 'personality'], msg: 'field required', type: 'missing' }] }
    expect(await failureMessage(reply(validation, 422), fallback)).toBe(fallback)
    expect(await failureMessage(reply({ detail: '   ' }, 400), fallback)).toBe(fallback)
    expect(await failureMessage(reply('<html>Internal Server Error</html>', 500), fallback)).toBe(fallback)
    expect(await failureMessage(reply(null, 500), fallback)).toBe(fallback)
  })
})

describe('reaching the backend', () => {
  it('turns a dead connection into a typed error with calm copy', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    const failure = await apiFetch('/persona').catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).code).toBe('offline')
    expect(messageFor(failure, 'fallback')).toBe(OFFLINE_MESSAGE)
  })

  it('never shows the text of an unexpected exception', () => {
    expect(messageFor(new TypeError('x is undefined'), 'Try again.')).toBe('Try again.')
    expect(messageFor('a string', 'Try again.')).toBe('Try again.')
  })
})
