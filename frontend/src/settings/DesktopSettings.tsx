// The desktop app's own settings: the two global shortcuts, starting with Windows,
// and what closing the window or clicking away does. The desktop shell holds them,
// not the backend, so in a browser tab this section says where to find them.
//
// A failed global shortcut is invisible (pressing it just does nothing), so the
// shell reports what it actually registered and each shortcut explains a mismatch.

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getPresenceState,
  isDesktop,
  setPresenceSettings,
  type PresenceSettings,
  type PresenceState,
} from '../desktop/tauri'
import { Button } from '../ui/Button'
import { Notice, Skeleton } from '../ui/Feedback'
import { Switch } from '../ui/Switch'
import { Group } from './Group'
import { HotkeyRow } from './HotkeyField'
import { registrationProblem } from './hotkeys'

const LOAD_FAILED = "Couldn't read the desktop settings. Try again in a moment."

export function DesktopSettings() {
  const desktop = isDesktop()
  const [presence, setPresence] = useState<PresenceState | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  // Only the newest change may settle the switches: an older answer arriving late
  // would put back a value you have already changed again.
  const latest = useRef(0)
  // What the shell last confirmed. A failed change goes back to this, never to a
  // value that was only ever shown while an earlier change was on its way.
  const confirmed = useRef<PresenceState | null>(null)

  const load = useCallback(async () => {
    // Cleared first, so "Try again" that fails again is shown and said again.
    setLoadFailed(false)
    try {
      const state = await getPresenceState()
      confirmed.current = state
      setPresence(state)
    } catch {
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    if (desktop) void load()
  }, [desktop, load])

  const apply = useCallback(
    async (next: PresenceSettings) => {
      if (!presence) return
      const mine = ++latest.current
      setPresence({ ...presence, settings: next })
      setFailed(null)
      try {
        const state = await setPresenceSettings(next)
        if (state) confirmed.current = state
        if (mine === latest.current && state) setPresence(state)
      } catch {
        if (mine !== latest.current) return
        setPresence(confirmed.current)
        setFailed("That change didn't stick. Try again in a moment.")
      }
    },
    [presence],
  )

  if (!desktop) {
    return (
      <Group title="Desktop" bare>
        <Notice>The global shortcuts, starting with Windows, and the tray belong to the desktop app. Open Veronica there to change them.</Notice>
      </Group>
    )
  }

  if (!presence) {
    return (
      <Group title="Shortcuts" bare={loadFailed}>
        {loadFailed ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void load()}>Try again</Button>}>
            {LOAD_FAILED}
          </Notice>
        ) : (
          <div role="status">
            <span className="visually-hidden">Loading the desktop settings</span>
            <Skeleton height={36} radius={8} />
          </div>
        )}
      </Group>
    )
  }

  const { settings, registrations } = presence
  const problem = (action: 'companion' | 'call') => registrationProblem(registrations.find((item) => item.action === action))

  return (
    <>
      <Group title="Shortcuts" lead="One keystroke reaches her from any app.">
        <HotkeyRow
          label="Summon Veronica"
          description="Opens the companion over whatever you're doing. Esc puts it away."
          value={settings.companion_hotkey}
          problem={problem('companion')}
          onChange={(companion_hotkey) => void apply({ ...settings, companion_hotkey })}
        />
        <HotkeyRow
          label="Start a call"
          description="Straight into a voice call, no mouse needed."
          value={settings.call_hotkey}
          problem={problem('call')}
          onChange={(call_hotkey) => void apply({ ...settings, call_hotkey })}
        />
      </Group>
      <Group title="Startup and windows">
        <Switch
          label="Start with Windows"
          description="She starts in the tray when you sign in, without opening a window."
          checked={settings.autostart}
          onChange={(autostart) => void apply({ ...settings, autostart })}
        />
        <Switch
          label="Keep running when the window closes"
          description="She stays in the tray. Quit from the tray menu to stop her completely."
          checked={settings.close_to_tray}
          onChange={(close_to_tray) => void apply({ ...settings, close_to_tray })}
        />
        <Switch
          label="Put the companion away when you click elsewhere"
          description="Off, it stays until you dismiss it."
          checked={settings.dismiss_on_blur}
          onChange={(dismiss_on_blur) => void apply({ ...settings, dismiss_on_blur })}
        />
      </Group>
      {failed && (
        <Notice tone="critical" live>
          {failed}
        </Notice>
      )}
    </>
  )
}
