// The page's policy: no code but the app's own, nothing loaded from elsewhere, nothing
// sent anywhere but her backend (scripts/__tests__/page.test.ts checks the page itself).

import { describe, expect, it } from 'vitest'
import { API, WS } from '../api'
import { BACKEND, CONTENT_SECURITY_POLICY } from '../csp'

function directive(name: string): string[] {
  const found = CONTENT_SECURITY_POLICY.split('; ').find((part) => part.startsWith(`${name} `))
  return found ? found.split(' ').slice(1) : []
}

describe('the page policy', () => {
  it('runs only the app’s own code, with no inline code and no eval', () => {
    expect(directive('script-src')).toEqual(["'self'"])
    expect(directive('style-src')).toEqual(["'self'"])
    expect(CONTENT_SECURITY_POLICY).not.toMatch(/unsafe-|\*|data:|https:/)
  })

  it('sends only to her backend, the address the app itself uses', () => {
    expect(BACKEND).toEqual([API, WS])
    expect(directive('connect-src')).toEqual(["'self'", API, WS, 'ipc:', 'http://ipc.localhost'])
  })

  it('embeds, frames and posts nothing, and no page can change where it loads from', () => {
    for (const name of ['object-src', 'frame-src', 'base-uri', 'form-action']) {
      expect(directive(name)).toEqual(["'none'"])
    }
  })
})
