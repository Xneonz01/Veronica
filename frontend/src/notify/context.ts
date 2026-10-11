// The notification Context lives in its own module so the provider file exports only a
// component (keeps React Fast Refresh happy).

import { createContext } from 'react'
import type { Notification } from './types'
import type { NotificationSettings } from './useNotificationSettings'

export interface NotificationContextValue {
  toasts: Notification[]
  history: Notification[]
  latest: Notification | null // the most recent toast; the call reacts to it
  unseen: number // notable writes not yet reviewed — drives the nav-rail pulse
  settings: NotificationSettings
  dismiss: (id: string) => void
  undo: (notification: Notification) => void
  markSeen: () => void
}

export const NotificationContext = createContext<NotificationContextValue | null>(null)
