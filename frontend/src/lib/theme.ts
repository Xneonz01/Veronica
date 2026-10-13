// The colour theme: System follows the operating system, Light and Dark pin one.
// The choice lives on this computer and is applied as data-theme on <html>, where
// the tokens' light-dark() values read it. Every open window follows a change at
// once: the companion hears it through the storage event.
//
// public/theme-init.js applies the stored choice before the first paint with a copy
// of `applyTheme`; the key and the attribute here must stay in step with it.

import { useSyncExternalStore } from 'react'
import { readPreference, writePreference } from './preference'

export type Theme = 'system' | 'light' | 'dark'

export const THEMES: readonly Theme[] = ['system', 'light', 'dark']
export const THEME_KEY = 'veronica.theme'

const listeners = new Set<() => void>()
let current: Theme = readPreference(THEME_KEY, THEMES, 'system')

/** Put a theme on the document. System removes the pin so color-scheme decides. */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  if (theme === 'system') delete root.dataset.theme
  else root.dataset.theme = theme
}

// Without this, every surface with a colour transition would fade to the new
// theme at its own speed, and the page would ripple instead of switching. A class
// whose rule is in base.css, not a <style> made here: the page's policy refuses those.
export const SWITCHING = 'theme-switching'

function withoutTransitions(change: () => void): void {
  const root = document.documentElement
  root.classList.add(SWITCHING)
  change()
  // Reading a computed style makes the browser apply the change while the pause
  // is still in place.
  void getComputedStyle(document.body).color
  window.setTimeout(() => root.classList.remove(SWITCHING), 0)
}

function adopt(theme: Theme): void {
  if (theme === current) return
  current = theme
  withoutTransitions(() => applyTheme(theme))
  listeners.forEach((listener) => listener())
}

export function setTheme(theme: Theme): void {
  writePreference(THEME_KEY, theme)
  adopt(theme)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getTheme(): Theme {
  return current
}

/** The chosen theme, which re-renders when it changes here or in another window. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme)
}

// Another window changed the theme (or the stored value was cleared). Registered
// once for the life of the window, like the store itself.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === THEME_KEY || event.key === null) {
      adopt(readPreference(THEME_KEY, THEMES, 'system'))
    }
  })
}
