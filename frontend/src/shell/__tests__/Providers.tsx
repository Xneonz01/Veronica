// The shell's providers with test data, so a piece of the shell renders alone.

import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { NotificationContext, type NotificationContextValue } from '../../notify/context'
import { WorkspaceContext, type Workspace } from '../../workspace/context'
import { ToastContext } from '../toast'
import { fakeNotifications, fakeWorkspace } from './fixtures'

export function Providers({
  children,
  workspace = fakeWorkspace(),
  notifications = fakeNotifications(),
  toast = vi.fn(),
}: {
  children: ReactNode
  workspace?: Workspace
  notifications?: NotificationContextValue
  toast?: (input: unknown) => void
}) {
  return (
    <MemoryRouter>
      <NotificationContext.Provider value={notifications}>
        <ToastContext.Provider value={{ show: toast }}>
          <WorkspaceContext.Provider value={workspace}>{children}</WorkspaceContext.Provider>
        </ToastContext.Provider>
      </NotificationContext.Provider>
    </MemoryRouter>
  )
}
