// Wrappers around the Tauri desktop shell.
//
// The command wrappers no-op in a plain browser, because most development and testing
// happens under `npm run dev`, and a component that threw outside Tauri couldn't be
// worked on there. The raw `invoke` and `listen` helpers need Tauri, so gate them (and
// anything else that can't work in a browser tab) on `isDesktop`.

import type { UnlistenFn } from '@tauri-apps/api/event'

export interface Registration {
  action: 'companion' | 'call'
  hotkey: string
  ok: boolean
  /** Set when `hotkey` is not what was asked for, so the UI can explain the difference. */
  requested: string | null
  code: string | null
}

export interface PresenceSettings {
  companion_hotkey: string
  call_hotkey: string
  autostart: boolean
  close_to_tray: boolean
  dismiss_on_blur: boolean
}

export interface PresenceState {
  settings: PresenceSettings
  registrations: Registration[]
}

/** True inside the Tauri webview. Tauri v2 injects `__TAURI_INTERNALS__`. */
export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const api = await import('@tauri-apps/api/core')
  return api.invoke<T>(command, args)
}

export async function listen<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<UnlistenFn> {
  const api = await import('@tauri-apps/api/event')
  return api.listen<T>(event, (e) => handler(e.payload))
}

export async function getPresenceState(): Promise<PresenceState | null> {
  if (!isDesktop()) return null
  return invoke<PresenceState>('presence_state')
}

export async function setPresenceSettings(
  settings: PresenceSettings,
): Promise<PresenceState | null> {
  if (!isDesktop()) return null
  return invoke<PresenceState>('set_presence_settings', { settings })
}

export async function hideCompanion(): Promise<void> {
  if (!isDesktop()) return
  await invoke('hide_companion')
}

/** Move into the main window, taking the conversation by id (it is persisted). */
export async function expandToMain(conversationId: number | null): Promise<void> {
  if (!isDesktop()) return
  await invoke('expand_to_main', { conversationId })
}
