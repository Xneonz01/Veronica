import { describe, expect, it } from 'vitest'
import type { Registration } from '../../desktop/tauri'
import { readChord, registrationProblem, REFUSED_COPY, shownKey } from '../hotkeys'
import { releaseDate } from '../releaseDate'

function press(key: string, code: string, held: Partial<Record<'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey', boolean>> = {}) {
  return { key, code, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...held }
}

function registration(overrides: Partial<Registration> = {}): Registration {
  return { action: 'companion', hotkey: 'Ctrl+Alt+Space', ok: true, requested: null, code: null, ...overrides }
}

describe('reading a pressed chord', () => {
  it('writes modifiers in one fixed order, whatever order they were pressed in', () => {
    expect(readChord(press('k', 'KeyK', { shiftKey: true, ctrlKey: true, altKey: true }))).toEqual({
      state: 'done',
      accelerator: 'Ctrl+Alt+Shift+K',
    })
    expect(readChord(press('x', 'KeyX', { metaKey: true, ctrlKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Super+X' })
  })

  it('waits while only modifiers are down, showing what is held so far', () => {
    expect(readChord(press('Control', 'ControlLeft', { ctrlKey: true }))).toEqual({ state: 'held', keys: ['Ctrl'] })
    expect(readChord(press('Alt', 'AltLeft', { ctrlKey: true, altKey: true }))).toEqual({ state: 'held', keys: ['Ctrl', 'Alt'] })
    expect(readChord(press('AltGraph', 'AltRight', { ctrlKey: true, altKey: true }))).toEqual({ state: 'held', keys: ['Ctrl', 'Alt'] })
  })

  it('refuses a bare key, and Shift on its own, which would take keys from every app', () => {
    expect(readChord(press('a', 'KeyA'))).toEqual({ state: 'refused', reason: 'bare' })
    expect(readChord(press('F5', 'F5'))).toEqual({ state: 'refused', reason: 'bare' })
    expect(readChord(press('K', 'KeyK', { shiftKey: true }))).toEqual({ state: 'refused', reason: 'shift_only' })
    expect(REFUSED_COPY.shift_only).toContain('capital letters')
  })

  it('names letters and digits by their place, so Shift and AltGr characters never leak in', () => {
    expect(readChord(press('!', 'Digit1', { ctrlKey: true, shiftKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Shift+1' })
    expect(readChord(press('@', 'KeyQ', { ctrlKey: true, altKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Alt+Q' })
  })

  it('names the space bar and keeps named keys as the browser gives them', () => {
    expect(readChord(press(' ', 'Space', { altKey: true }))).toEqual({ state: 'done', accelerator: 'Alt+Space' })
    expect(readChord(press('F9', 'F9', { ctrlKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+F9' })
    expect(readChord(press('ArrowUp', 'ArrowUp', { ctrlKey: true, altKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Alt+ArrowUp' })
  })

  it('names punctuation and the numpad by place, in words the desktop shell reads', () => {
    // By character these would be "?" (unreadable to it) and "+" (which splits a shortcut).
    expect(readChord(press('?', 'Slash', { ctrlKey: true, shiftKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Shift+Slash' })
    expect(readChord(press('+', 'NumpadAdd', { ctrlKey: true, altKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Alt+NumpadAdd' })
    expect(readChord(press('7', 'Numpad7', { ctrlKey: true }))).toEqual({ state: 'done', accelerator: 'Ctrl+Numpad7' })
    expect(shownKey('Slash')).toBe('/')
    expect(shownKey('NumpadAdd')).toBe('Num +')
    expect(shownKey('Numpad7')).toBe('Num 7')
    expect(shownKey('Super')).toBe('Win')
  })

  it("refuses a key the desktop shell can't register", () => {
    expect(readChord(press('MediaPlayPause', 'MediaPlayPause', { ctrlKey: true }))).toEqual({ state: 'refused', reason: 'unsupported' })
    expect(REFUSED_COPY.unsupported).toContain("can't be part of a shortcut")
  })
})

describe('explaining a registration', () => {
  it('says nothing when the shortcut works as asked', () => {
    expect(registrationProblem(registration())).toBeNull()
    expect(registrationProblem(undefined)).toBeNull()
  })

  it('names both combinations when she fell back to another one', () => {
    const message = registrationProblem(registration({ code: 'fell_back', requested: 'Alt+Space', hotkey: 'Ctrl+Alt+Space' }))
    expect(message).toBe("Alt+Space is taken by another app, so she's on Ctrl+Alt+Space instead.")
  })

  it('has words for every code the desktop shell sends, and for one it does not know', () => {
    for (const code of ['already_taken', 'same_as_companion', 'no_modifier', 'empty', 'unparseable', 'something_new']) {
      const message = registrationProblem(registration({ ok: false, code }))
      expect(message, code).toBeTruthy()
      expect(message).not.toContain(code)
    }
    expect(registrationProblem(registration({ ok: false }))).toBeTruthy()
  })
})

describe('release dates', () => {
  it('reads an ISO date as a short date and leaves anything else alone', () => {
    // ICU writes September as "Sep" or "Sept" depending on its version.
    expect(releaseDate('2026-09-20')).toMatch(/^20 Sept? 2026$/)
    expect(releaseDate('2026-01-05')).toBe('5 Jan 2026')
    expect(releaseDate('unreleased')).toBe('unreleased')
    expect(releaseDate('')).toBe('')
  })
})
