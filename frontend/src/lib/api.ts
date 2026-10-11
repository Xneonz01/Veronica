// Shared fetch helpers. Error copy shown to the user comes from here, not from raw
// server responses:
//  1. A network failure becomes a typed `offline` error instead of the raw
//     "TypeError: Failed to fetch".
//  2. Response bodies are untrusted. We map the backend's `code` to copy and only
//     pass through a `detail` that is really a string (FastAPI validation errors
//     put an array there, which would crash React if rendered).

export const API = 'http://localhost:8000'
/** The same backend, for the sockets: chat, the call and the event lane. */
export const WS = API.replace(/^http/, 'ws')

/** Backend error codes -> user-facing copy. Unknown codes fall back to the caller's. */
const CODE_COPY: Record<string, string> = {
  invalid_input: "That value isn't valid — check the highlighted field.",
  internal: "Something went wrong on Veronica's side. Try again in a moment.",
  untrusted_origin: 'That request was blocked for security.',
}

export const OFFLINE_MESSAGE = "Can't reach Veronica right now — is the backend running?"

export class ApiError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

/** `fetch`, but a dead backend becomes a typed error instead of a raw TypeError. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API}${path}`, init)
  } catch {
    throw new ApiError('offline', OFFLINE_MESSAGE)
  }
}

// When a route gives no detail of its own, the server fills in the status phrase
// ("Not Found" for an address this backend doesn't have, say an older one). That is
// server text, not copy.
const STATUS_PHRASES = new Set([
  'Bad Request',
  'Unauthorized',
  'Forbidden',
  'Not Found',
  'Method Not Allowed',
  'Conflict',
  'Payload Too Large',
  'Content Too Large',
  'Unprocessable Entity',
  'Unprocessable Content',
  'Too Many Requests',
  'Internal Server Error',
  'Bad Gateway',
  'Service Unavailable',
  'Gateway Timeout',
])

/** A safe, user-facing message for a failed response, never server text of unknown shape. */
export async function failureMessage(res: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object') {
      const { code, detail } = body as { code?: unknown; detail?: unknown }
      if (typeof code === 'string' && CODE_COPY[code]) return CODE_COPY[code]
      // Some routes send curated copy in `detail`; accept it only if it is a string
      // (a validation error array must never reach the DOM) and not a stock phrase.
      if (typeof detail === 'string' && detail.trim() && !STATUS_PHRASES.has(detail) && detail !== res.statusText) return detail
    }
  } catch {
    // no JSON body — fall through
  }
  return fallback
}

/** The message to show for any thrown value, without leaking exception text. */
export function messageFor(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}
