// Consumer hook for the notification system: the toast queue, the awareness history,
// dismiss/undo, and `latest` for surfaces (Call, Chat) that react to the newest one.

import { useContext } from 'react'
import { NotificationContext } from './context'

export function useNotifications() {
  const ctx = useContext(NotificationContext)
  if (ctx === null) {
    throw new Error('useNotifications must be used within a NotificationProvider')
  }
  return ctx
}
