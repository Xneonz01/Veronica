// The page the app loads into holds no code or style of its own: its policy
// (src/lib/csp.ts) refuses inline code, so anything written there would silently not
// run in the built app. scripts/check-page.ts checks the built page the same way.

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(import.meta.dirname, '..', '..', 'index.html'), 'utf8')

describe('the page', () => {
  it('loads every script from a file', () => {
    const scripts = [...page.matchAll(/<script\b[^>]*>/g)].map((match) => match[0])
    expect(scripts.length).toBeGreaterThan(0)
    expect(scripts.filter((tag) => !/\ssrc=/.test(tag))).toEqual([])
  })

  it('has no inline style or event handler', () => {
    expect(page).not.toMatch(/<style\b|\sstyle=|\son[a-z]+=/i)
  })
})
