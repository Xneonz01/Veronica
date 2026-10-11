// Notification settings. One instance lives in the NotificationProvider and is shared
// via context, so a change on any surface shows everywhere without a refresh. Changing
// `sounds` also updates the sound module's global mute.

import { useCallback, useEffect, useState } from 'react'
import { setSoundsEnabled } from '../audio/notificationSounds'
import { readSounds, readVerbosity, writeSounds, writeVerbosity, type Verbosity } from './settings'

export interface NotificationSettings {
  verbosity: Verbosity
  sounds: boolean
  setVerbosity: (value: Verbosity) => void
  setSounds: (on: boolean) => void
}

export function useNotificationSettings(): NotificationSettings {
  const [verbosity, setVerbosityState] = useState<Verbosity>(() => readVerbosity())
  const [sounds, setSoundsState] = useState<boolean>(() => readSounds())

  useEffect(() => {
    setSoundsEnabled(sounds) // keep the module mute in sync (incl. the persisted value on mount)
  }, [sounds])

  const setVerbosity = useCallback((value: Verbosity) => {
    setVerbosityState(value)
    writeVerbosity(value)
  }, [])

  const setSounds = useCallback((on: boolean) => {
    setSoundsState(on)
    writeSounds(on)
  }, [])

  return { verbosity, sounds, setVerbosity, setSounds }
}
