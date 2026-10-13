// The page's Content-Security-Policy. What she reads (a page, a document, a tool's
// output) only ever reaches the screen as text React escapes, and a check keeps it that
// way (scripts/__tests__/sinks.test.ts). This is the wall behind that: if text ever did
// become markup, it could run no code, load nothing from elsewhere, and send nothing
// anywhere but this app and her backend.
//
// It goes into the built page only (vite.config.ts): the dev server's hot reload runs
// inline code. A packaged build has to be checked by hand before a release, because a
// policy set in the page has no report-only mode: a mistake shows as a broken window,
// not as a warning.

/** Her backend, for requests and for the sockets (`lib/api.ts`). */
export const BACKEND = ['http://localhost:8000', 'ws://localhost:8000']
/** The Tauri shell's bridge: `ipc:`, and http://ipc.localhost on Windows. */
const SHELL = ['ipc:', 'http://ipc.localhost']

export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self'",
  // A voice sample plays from the bytes the backend sent (audio/useVoices.ts).
  "media-src 'self' blob:",
  `connect-src 'self' ${[...BACKEND, ...SHELL].join(' ')}`,
  "worker-src 'self'",
  "object-src 'none'",
  "frame-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ')
