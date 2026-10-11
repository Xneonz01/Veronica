// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PresenceSettings, PresenceState } from '../../desktop/tauri'
import { DesktopSettings } from '../DesktopSettings'

const shell = vi.hoisted(() => ({
  desktop: true,
  load: vi.fn<() => Promise<PresenceState | null>>(),
  save: vi.fn<(settings: PresenceSettings) => Promise<PresenceState | null>>(),
}))
vi.mock('../../desktop/tauri', () => ({
  isDesktop: () => shell.desktop,
  getPresenceState: () => shell.load(),
  setPresenceSettings: (settings: PresenceSettings) => shell.save(settings),
}))

const SETTINGS: PresenceSettings = {
  companion_hotkey: 'Ctrl+Alt+Space',
  call_hotkey: 'Ctrl+Alt+V',
  autostart: true,
  close_to_tray: true,
  dismiss_on_blur: false,
}

function state(settings: PresenceSettings = SETTINGS, problems: PresenceState['registrations'] = []): PresenceState {
  return {
    settings,
    registrations: problems.length
      ? problems
      : [
          { action: 'companion', hotkey: settings.companion_hotkey, ok: true, requested: null, code: null },
          { action: 'call', hotkey: settings.call_hotkey, ok: true, requested: null, code: null },
        ],
  }
}

function press(key: string, code: string, held: Partial<KeyboardEventInit> = {}) {
  fireEvent.keyDown(window, { key, code, ...held })
}

beforeEach(() => {
  shell.desktop = true
  shell.load.mockReset().mockResolvedValue(state())
  shell.save.mockReset().mockImplementation(async (settings) => state(settings))
})
afterEach(cleanup)

const summon = () => screen.findByRole('button', { name: /^Summon Veronica: / })

describe('desktop settings', () => {
  it('says where they live when the page is not in the desktop app', () => {
    shell.desktop = false
    render(<DesktopSettings />)
    expect(screen.getByText(/belong to the desktop app/)).toBeTruthy()
    expect(shell.load).not.toHaveBeenCalled()
  })

  it('sets a shortcut from the keys pressed, showing the modifiers as they go down', async () => {
    render(<DesktopSettings />)
    await userEvent.click(await summon())
    expect(screen.getByText('Press the new combination. Esc cancels.')).toBeTruthy()
    press('Control', 'ControlLeft', { ctrlKey: true })
    press('Alt', 'AltLeft', { ctrlKey: true, altKey: true })
    expect((await summon()).textContent).toContain('Ctrl')
    await act(async () => press('k', 'KeyK', { ctrlKey: true, altKey: true }))
    expect(shell.save).toHaveBeenCalledWith({ ...SETTINGS, companion_hotkey: 'Ctrl+Alt+K' })
    expect((await summon()).getAttribute('aria-label')).toBe('Summon Veronica: Ctrl Alt K. Change it')
  })

  it('refuses a bare key, keeps listening, and Escape leaves the shortcut as it was', async () => {
    render(<DesktopSettings />)
    await userEvent.click(await summon())
    press('a', 'KeyA')
    expect(screen.getByText(/or that key would stop working in every other app/)).toBeTruthy()
    press('K', 'KeyK', { shiftKey: true })
    expect(screen.getByText(/Shift alone would take capital letters/)).toBeTruthy()
    press('Escape', 'Escape')
    expect(shell.save).not.toHaveBeenCalled()
    expect((await summon()).getAttribute('aria-label')).toBe('Summon Veronica: Ctrl Alt Space. Change it')
  })

  it('does not save a shortcut pressed again unchanged', async () => {
    render(<DesktopSettings />)
    await userEvent.click(await summon())
    press(' ', 'Space', { ctrlKey: true, altKey: true })
    expect(shell.save).not.toHaveBeenCalled()
  })

  it('keeps every key away from the rest of the page while listening', async () => {
    const elsewhere = vi.fn()
    document.addEventListener('keydown', elsewhere)
    render(<DesktopSettings />)
    await userEvent.click(await summon())
    press('Tab', 'Tab')
    press('k', 'KeyK', { ctrlKey: true })
    expect(elsewhere).not.toHaveBeenCalled()
    document.removeEventListener('keydown', elsewhere)
  })

  it('explains a shortcut the desktop shell could not register', async () => {
    shell.load.mockResolvedValue(
      state(SETTINGS, [
        { action: 'companion', hotkey: 'Ctrl+Alt+Space', ok: true, requested: 'Alt+Space', code: 'fell_back' },
        { action: 'call', hotkey: 'Ctrl+Alt+V', ok: false, requested: null, code: 'already_taken' },
      ]),
    )
    render(<DesktopSettings />)
    expect(await screen.findByText("Alt+Space is taken by another app, so she's on Ctrl+Alt+Space instead.")).toBeTruthy()
    expect(screen.getByText('Another app is already using Ctrl+Alt+V. Pick a different combination.')).toBeTruthy()
  })

  it('puts a switch back and says so when the change does not stick', async () => {
    shell.save.mockRejectedValue(new Error('ipc'))
    render(<DesktopSettings />)
    const autostart = await screen.findByRole('switch', { name: 'Start with Windows' })
    await userEvent.click(autostart)
    expect(await screen.findByText("That change didn't stick. Try again in a moment.")).toBeTruthy()
    expect(autostart.getAttribute('aria-checked')).toBe('true')
  })

  it('lets only the newest of two quick changes settle the switches', async () => {
    let answerFirst: (value: PresenceState) => void = () => undefined
    shell.save
      .mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)))
      .mockImplementationOnce(async (settings) => state(settings))
    render(<DesktopSettings />)
    const tray = await screen.findByRole('switch', { name: 'Keep running when the window closes' })
    await userEvent.click(tray)
    await userEvent.click(tray)
    await act(async () => answerFirst(state({ ...SETTINGS, close_to_tray: false })))
    expect(tray.getAttribute('aria-checked')).toBe('true')
  })

  it('goes back to what the shell last confirmed when two quick changes both fail', async () => {
    // Windows refuses autostart, so every change carrying it fails.
    let failFirst: (reason: Error) => void = () => undefined
    shell.save
      .mockImplementationOnce(() => new Promise((_, reject) => (failFirst = reject)))
      .mockRejectedValueOnce(new Error('ipc'))
    render(<DesktopSettings />)
    const autostart = await screen.findByRole('switch', { name: 'Start with Windows' })
    const blur = screen.getByRole('switch', { name: 'Put the companion away when you click elsewhere' })
    await userEvent.click(autostart) // off, on its way
    await userEvent.click(blur) // carries autostart off with it, and fails
    await act(async () => failFirst(new Error('ipc')))
    expect(autostart.getAttribute('aria-checked')).toBe('true') // as the shell still has it
    expect(blur.getAttribute('aria-checked')).toBe('false')
  })

  it('offers another try when the settings cannot be read', async () => {
    shell.load.mockRejectedValueOnce(new Error('ipc'))
    render(<DesktopSettings />)
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await summon()).toBeTruthy()
  })
})
