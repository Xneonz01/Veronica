// Keyboard shortcuts, written once for both platforms: Ctrl on Windows and Linux,
// Command on a Mac, in both the key that triggers them and the hint that names them.

import { useEffect, useLayoutEffect, useRef } from 'react'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)

/** The modifier's name as the keyboard prints it. */
export const MOD = isMac ? '⌘' : 'Ctrl'

export type Shortcut = { key: string; shift?: boolean }

export function matches(event: KeyboardEvent, shortcut: Shortcut): boolean {
  const mod = isMac ? event.metaKey : event.ctrlKey
  return mod && event.shiftKey === (shortcut.shift ?? false) && !event.altKey && event.key.toLowerCase() === shortcut.key
}

/** How a shortcut is written in a hint: "Ctrl K", "Ctrl Shift O", "⌘K". */
export function label(shortcut: Shortcut): string {
  const parts = [MOD, ...(shortcut.shift ? [isMac ? '⇧' : 'Shift'] : []), shortcut.key.toUpperCase()]
  return parts.join(isMac ? '' : ' ')
}

/** The shortcut as `aria-keyshortcuts` writes it: "Control+K", or "Meta+K" on a Mac. */
export function ariaKeys(shortcut: Shortcut): string {
  return [isMac ? 'Meta' : 'Control', ...(shortcut.shift ? ['Shift'] : []), shortcut.key.toUpperCase()].join('+')
}

/** Runs `run` when the shortcut is pressed anywhere in the window. */
export function useShortcut(shortcut: Shortcut, run: () => void): void {
  const latest = useRef(run)
  useLayoutEffect(() => {
    latest.current = run
  })
  const { key, shift } = shortcut
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!matches(event, { key, shift })) return
      event.preventDefault()
      latest.current()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [key, shift])
}

export const SHORTCUTS = {
  palette: { key: 'k' },
  newChat: { key: 'o', shift: true },
  save: { key: 's' },
} satisfies Record<string, Shortcut>
