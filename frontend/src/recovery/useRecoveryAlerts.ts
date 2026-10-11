// A recovery key made by something other than this app (a program on this computer,
// which the local API lets in: SECURITY.md) replaces the key the person saved, and they
// should know at once, in every window. One made in the app says nothing here: the
// person who made it is looking at it.

import { useAppEvents } from '../audio/useAppEvents'
import { useToasts } from '../shell/toast'

export function useRecoveryAlerts(): void {
  const toasts = useToasts()
  useAppEvents({
    onRecoveryUpdated: ({ via, made }) => {
      if (via !== 'api' || !made) return
      const when = new Date(made)
      const at = Number.isNaN(when.getTime())
        ? ''
        : ` at ${when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`
      toasts.show({
        tone: 'critical',
        title: 'Another program made a new recovery key',
        body:
          `A recovery key was made${at} outside this app, and the one you saved no longer opens her data. ` +
          "If it wasn't you, make a new one in Settings > Privacy.",
      })
    },
  })
}
