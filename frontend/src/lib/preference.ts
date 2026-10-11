// Small per-window conveniences kept in localStorage: the sidebar's width, the
// theme. Storage can be missing or throw (a locked-down profile, a full disk), so
// every read falls back and every write is best effort; nothing here is data the
// app can't lose.

export function readPreference<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const saved = localStorage.getItem(key)
    return allowed.find((value) => value === saved) ?? fallback
  } catch {
    return fallback
  }
}

export function writePreference(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // the choice still applies until the window closes
  }
}
