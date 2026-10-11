import { describe, expect, it } from 'vitest'
import { label, matches } from '../shortcuts'

// Vitest runs in node, where there is no navigator: the Windows and Linux forms.
function key(init: Partial<KeyboardEvent>): KeyboardEvent {
  return { ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, key: '', ...init } as KeyboardEvent
}

describe('shortcuts', () => {
  it('matches the key with Ctrl, and only with exactly the modifiers asked for', () => {
    expect(matches(key({ ctrlKey: true, key: 'k' }), { key: 'k' })).toBe(true)
    expect(matches(key({ key: 'k' }), { key: 'k' })).toBe(false)
    expect(matches(key({ ctrlKey: true, shiftKey: true, key: 'K' }), { key: 'k' })).toBe(false)
    expect(matches(key({ ctrlKey: true, shiftKey: true, key: 'O' }), { key: 'o', shift: true })).toBe(true)
    expect(matches(key({ ctrlKey: true, altKey: true, key: 'k' }), { key: 'k' })).toBe(false)
  })

  it('writes a hint the way the keyboard prints it', () => {
    expect(label({ key: 'k' })).toBe('Ctrl K')
    expect(label({ key: 'o', shift: true })).toBe('Ctrl Shift O')
  })
})
