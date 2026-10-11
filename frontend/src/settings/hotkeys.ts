// Global shortcuts as the desktop shell writes them ("Ctrl+Alt+Space"), read from
// the keys you press, and what to say when one can't be registered. The shell sends
// a code; the words live here, as for every other error in the app.

import type { Registration } from '../desktop/tauri'

type KeyPress = Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>

export type Chord =
  /** Only modifiers so far: someone mid-press. */
  | { state: 'held'; keys: string[] }
  /** A key that would be taken from every app on the computer, or one the desktop
      shell can't register at all. */
  | { state: 'refused'; reason: 'bare' | 'shift_only' | 'unsupported' }
  | { state: 'done'; accelerator: string }

const MODIFIER_KEYS = new Set(['Control', 'Alt', 'AltGraph', 'Shift', 'Meta', 'OS'])

function modifiers(event: KeyPress): string[] {
  const held: string[] = []
  // A fixed order, so the same chord always gives the same string as the stored one.
  if (event.ctrlKey) held.push('Ctrl')
  if (event.altKey) held.push('Alt')
  if (event.shiftKey) held.push('Shift')
  if (event.metaKey) held.push('Super')
  return held
}

// Keys named by their place on the keyboard (`KeyboardEvent.code`), which are also
// the names the desktop shell's parser takes (global-hotkey 0.8, verified against
// its source). Named by the character instead, Shift+/ would be "?", which it
// can't read, and numpad plus a "+" that splits the shortcut in two.
const BY_PLACE = new Set([
  'Backquote', 'Backslash', 'BracketLeft', 'BracketRight', 'Comma', 'Equal', 'Minus',
  'Period', 'Quote', 'Semicolon', 'Slash', 'NumpadAdd', 'NumpadSubtract',
  'NumpadMultiply', 'NumpadDivide', 'NumpadDecimal', 'ArrowUp', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'Space', 'Enter', 'Tab', 'Home', 'End', 'PageUp',
  'PageDown', 'Insert', 'Delete',
])

/** The key's name, or null for one the desktop shell can't register. Letters and
    digits come from the key's place too, so Shift+1 is "1", not "!", and a layout's
    AltGr characters don't leak in. */
function keyName(event: KeyPress): string | null {
  const letterOrDigit = /^(?:Key([A-Z])|Digit(\d))$/.exec(event.code)
  if (letterOrDigit) return letterOrDigit[1] ?? letterOrDigit[2] ?? null
  if (/^(?:F(?:[1-9]|1\d|2[0-4])|Numpad\d)$/.test(event.code) || BY_PLACE.has(event.code)) return event.code
  return null
}

const SHOWN: Record<string, string> = {
  Super: 'Win', Backquote: '`', Backslash: '\\', BracketLeft: '[', BracketRight: ']',
  Comma: ',', Equal: '=', Minus: '-', Period: '.', Quote: "'", Semicolon: ';', Slash: '/',
  NumpadAdd: 'Num +', NumpadSubtract: 'Num −', NumpadMultiply: 'Num ×', NumpadDivide: 'Num ÷',
  NumpadDecimal: 'Num .', ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←', ArrowRight: '→',
  PageUp: 'Page Up', PageDown: 'Page Down',
}

/** A stored key name as it is shown and read out: "Slash" is "/", "Super" is "Win". */
export function shownKey(name: string): string {
  const numpadDigit = /^Numpad(\d)$/.exec(name)
  return numpadDigit ? `Num ${numpadDigit[1]}` : (SHOWN[name] ?? name)
}

export function readChord(event: KeyPress): Chord {
  const held = modifiers(event)
  if (MODIFIER_KEYS.has(event.key)) return { state: 'held', keys: held }
  if (held.length === 0) return { state: 'refused', reason: 'bare' }
  // Shift+K would take every capital K typed anywhere.
  if (held.length === 1 && held[0] === 'Shift') return { state: 'refused', reason: 'shift_only' }
  const key = keyName(event)
  if (key === null) return { state: 'refused', reason: 'unsupported' }
  return { state: 'done', accelerator: [...held, key].join('+') }
}

export const REFUSED_COPY: Record<'bare' | 'shift_only' | 'unsupported', string> = {
  bare: 'Add Ctrl, Alt or the Windows key, or that key would stop working in every other app.',
  shift_only: 'Shift alone would take capital letters from every other app. Add Ctrl or Alt.',
  unsupported: "That key can't be part of a shortcut. Try a letter, a number or a function key.",
}

/** What went wrong with a shortcut, in words, or null when it is working as asked. */
export function registrationProblem(registration: Registration | undefined): string | null {
  if (!registration) return null
  const asked = registration.requested ?? registration.hotkey
  switch (registration.code) {
    case null:
      return registration.ok ? null : "That combination couldn't be registered. Try another one."
    case 'fell_back':
      return `${asked} is taken by another app, so she's on ${registration.hotkey} instead.`
    case 'already_taken':
      return `Another app is already using ${asked}. Pick a different combination.`
    case 'same_as_companion':
      return "That's already the shortcut that summons her. Pick a different one."
    case 'no_modifier':
      return REFUSED_COPY.bare
    case 'empty':
      return 'Pick a combination.'
    case 'unparseable':
      return `${asked} can't be used as a shortcut. Try a letter, a number or an F key with Ctrl or Alt.`
    default:
      return "That combination couldn't be registered. Try another one."
  }
}
