// The window's frame: the sidebar or its rail, the content sheet the current page
// sits on, and the layers that must reach every page: the permission prompt, the
// toasts, the palette, the memory activity. The chat session and the live call
// are held here too, so neither ends when you change page.

import { useCallback, useRef, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { CallProvider } from '../call/CallProvider'
import { useCallStatus } from '../call/context'
import { useDesktopEvents } from '../desktop/useDesktopEvents'
import { readPreference, writePreference } from '../lib/preference'
import { SHORTCUTS, useShortcut } from '../lib/shortcuts'
import { setTheme, useTheme } from '../lib/theme'
import { useMediaQuery } from '../lib/useMediaQuery'
import { NotificationProvider } from '../notify/NotificationProvider'
import { useNotifications } from '../notify/useNotifications'
import { useRecoveryAlerts } from '../recovery/useRecoveryAlerts'
import { useWorkspace } from '../workspace/context'
import { WorkspaceProvider } from '../workspace/WorkspaceProvider'
import { ActivitySheet } from './ActivitySheet'
import styles from './AppShell.module.css'
import { CommandPalette } from './CommandPalette'
import { OfflineBanner } from './OfflineBanner'
import { paletteItems } from './paletteItems'
import { PermissionPrompts } from './PermissionPrompts'
import { ProblemBanner } from './ProblemBanner'
import { Sidebar, type SidebarLayout } from './Sidebar'
import { useToasts } from './toast'
import { ToastProvider } from './Toasts'

const SIDEBAR_KEY = 'veronica.sidebar'
const LAYOUTS: readonly SidebarLayout[] = ['full', 'rail']
// Narrower than the desktop window can go (a browser tab can), the sidebar folds
// to its rail, or the page would be squeezed to a sliver beside it.
const NARROW = '(max-width: 879px)'

export function AppShell() {
  // The tray and the global hotkeys navigate through here.
  useDesktopEvents()
  return (
    <NotificationProvider>
      <ToastProvider>
        <CallProvider>
          <WorkspaceProvider>
            <Frame />
          </WorkspaceProvider>
        </CallProvider>
      </ToastProvider>
    </NotificationProvider>
  )
}

function Frame() {
  const [layout, setLayout] = useState<SidebarLayout>(() => readPreference(SIDEBAR_KEY, LAYOUTS, 'full'))
  const narrow = useMediaQuery(NARROW)
  const shown = narrow ? 'rail' : layout
  const [palette, setPalette] = useState(false)
  const [activity, setActivity] = useState(false)
  const workspace = useWorkspace()
  const { markSeen } = useNotifications()
  const toasts = useToasts()
  const call = useCallStatus()
  const navigate = useNavigate()
  const theme = useTheme()
  const content = useRef<HTMLElement>(null)
  useRecoveryAlerts()

  const toggleSidebar = useCallback(() => {
    const next = layout === 'full' ? 'rail' : 'full'
    setLayout(next)
    writePreference(SIDEBAR_KEY, next)
  }, [layout])

  const openActivity = useCallback(() => {
    setActivity(true)
    markSeen()
  }, [markSeen])

  const ask = useCallback(
    (question: string) => {
      if (workspace.ask(question)) return
      toasts.show({
        tone: 'critical',
        title: "Couldn't send that",
        body: workspace.chatUnavailable
          ? "Chat is paused while her memory can't be opened. The notice at the top says what to do."
          : 'The connection to Veronica is down. It reconnects by itself; ask again in a moment.',
      })
    },
    [workspace, toasts],
  )

  const items = useCallback(
    (query: string) =>
      paletteItems(query, {
        workspace,
        go: (path: string, state?: unknown) => void navigate(path, { state }),
        ask,
        call,
        railed: shown === 'rail',
        toggleSidebar: narrow ? undefined : toggleSidebar,
        openActivity,
        theme,
        setTheme,
      }),
    [workspace, navigate, ask, call, shown, narrow, toggleSidebar, openActivity, theme],
  )

  useShortcut(SHORTCUTS.palette, () => setPalette((open) => !open))
  useShortcut(SHORTCUTS.newChat, workspace.newChat)

  return (
    <div className={styles.frame} data-sidebar={shown}>
      <button type="button" className={styles.skip} onClick={() => content.current?.focus()}>
        Skip to content
      </button>
      <Sidebar
        layout={shown}
        onToggle={narrow ? undefined : toggleSidebar}
        onSearch={() => setPalette(true)}
        onActivity={openActivity}
      />
      <main ref={content} tabIndex={-1} className={styles.sheet}>
        <OfflineBanner />
        <ProblemBanner />
        <div className={styles.page}>
          <Outlet />
        </div>
        <PermissionPrompts />
      </main>
      <CommandPalette open={palette} onClose={() => setPalette(false)} items={items} />
      <ActivitySheet open={activity} onClose={() => setActivity(false)} />
    </div>
  )
}
