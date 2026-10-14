// @vitest-environment jsdom
// The Expressive voice switch: on until switched off, kept on this computer, and every
// change passed on at once, which is how a call in progress hears of it.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { onExpressiveChange, readExpressive, writeExpressive } from '../../notify/settings'
import { ExpressiveChoice } from '../ExpressiveChoice'

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('Expressive voice', () => {
  it('is on until it is switched off', () => {
    render(<ExpressiveChoice />)
    expect(screen.getByRole('switch', { name: 'Expressive voice' }).getAttribute('aria-checked')).toBe('true')
  })

  it('keeps the choice, and says so to whoever is listening', () => {
    const heard = vi.fn()
    const stop = onExpressiveChange(heard)
    render(<ExpressiveChoice />)
    const toggle = screen.getByRole('switch', { name: 'Expressive voice' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(readExpressive()).toBe(false)
    expect(heard).toHaveBeenLastCalledWith(false)
    stop()
    fireEvent.click(toggle)
    expect(readExpressive()).toBe(true)
    expect(heard).toHaveBeenCalledTimes(1)
  })

  it('starts as it was left', () => {
    writeExpressive(false)
    render(<ExpressiveChoice />)
    expect(screen.getByRole('switch', { name: 'Expressive voice' }).getAttribute('aria-checked')).toBe('false')
  })

  it('follows a change made in the other place it is shown', () => {
    render(<ExpressiveChoice />)
    act(() => writeExpressive(false))
    expect(screen.getByRole('switch', { name: 'Expressive voice' }).getAttribute('aria-checked')).toBe('false')
  })
})
