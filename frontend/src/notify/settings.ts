// Persisted notification settings (same localStorage pattern as audio/useAudioDevices).
// Verbosity controls toasts; the sounds toggle is independent. The plain read functions
// let code outside React state (e.g. `useRealtimeCall` building `call_start`) read the
// persisted values directly.

export type Verbosity = 'loud' | 'subtle' | 'silent'

const VERBOSITY_KEY = 'veronica.notifications.verbosity'
const SOUNDS_KEY = 'veronica.notifications.sounds'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // private mode / restricted webview — fall back to defaults
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // persistence is best-effort
  }
}

export function readVerbosity(): Verbosity {
  const value = read(VERBOSITY_KEY)
  return value === 'loud' || value === 'silent' ? value : 'subtle' // default: subtle
}

export function writeVerbosity(value: Verbosity): void {
  write(VERBOSITY_KEY, value)
}

export function readSounds(): boolean {
  return read(SOUNDS_KEY) !== 'off' // default: on
}

export function writeSounds(on: boolean): void {
  write(SOUNDS_KEY, on ? 'on' : 'off')
}

// Greeting settings. Owned by the frontend and sent in the call_start control message,
// so a change applies to the next call with no round-trip.
export interface GreetingSettings {
  enabled: boolean
  length: 'brief' | 'warm'
  callback: boolean
}

const GREETING_ENABLED_KEY = 'veronica.greeting.enabled'
const GREETING_LENGTH_KEY = 'veronica.greeting.length'
const GREETING_CALLBACK_KEY = 'veronica.greeting.callback'

export function readGreeting(): GreetingSettings {
  return {
    enabled: read(GREETING_ENABLED_KEY) !== 'off', // default: on
    length: read(GREETING_LENGTH_KEY) === 'brief' ? 'brief' : 'warm',
    callback: read(GREETING_CALLBACK_KEY) !== 'off', // default: on
  }
}

export function writeGreeting(value: GreetingSettings): void {
  write(GREETING_ENABLED_KEY, value.enabled ? 'on' : 'off')
  write(GREETING_LENGTH_KEY, value.length)
  write(GREETING_CALLBACK_KEY, value.callback ? 'on' : 'off')
}
