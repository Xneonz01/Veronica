// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The theme module reads the stored choice once, when it loads, so each test
// loads a fresh copy after setting up storage.
async function loadTheme() {
  vi.resetModules()
  return import('../theme')
}

beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
  // A switch in the test before may still hold its pause on real timers.
  document.head.querySelectorAll('style').forEach((style) => style.remove())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the theme', () => {
  it('starts on System, which leaves the choice to the operating system', async () => {
    const { applyTheme, useTheme } = await loadTheme()
    const { result } = renderHook(() => useTheme())
    expect(result.current).toBe('system')
    applyTheme('dark')
    applyTheme('system')
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('ignores a stored value it does not know', async () => {
    localStorage.setItem('veronica.theme', 'sepia')
    const { useTheme } = await loadTheme()
    expect(renderHook(() => useTheme()).result.current).toBe('system')
  })

  it('applies a choice at once, remembers it, and tells every reader', async () => {
    const { setTheme, useTheme } = await loadTheme()
    const { result } = renderHook(() => useTheme())
    act(() => setTheme('light'))
    expect(result.current).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('veronica.theme')).toBe('light')
  })

  it('pauses transitions for the switch, so surfaces change together', async () => {
    vi.useFakeTimers()
    const { setTheme } = await loadTheme()
    setTheme('dark')
    const pause = [...document.head.querySelectorAll('style')].find((style) => style.textContent?.includes('transition:none'))
    expect(pause).toBeDefined()
    vi.runAllTimers()
    expect(pause?.isConnected).toBe(false)
    vi.useRealTimers()
  })

  it('follows a change made in another window', async () => {
    const { useTheme } = await loadTheme()
    const { result } = renderHook(() => useTheme())
    localStorage.setItem('veronica.theme', 'dark')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'veronica.theme', newValue: 'dark' }))
    })
    expect(result.current).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('still switches when storage refuses to keep the choice', async () => {
    const { setTheme, useTheme } = await loadTheme()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    const { result } = renderHook(() => useTheme())
    act(() => setTheme('dark'))
    expect(result.current).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
