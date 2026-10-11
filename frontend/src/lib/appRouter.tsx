// The app router. Hash history works inside the packaged Tauri webview, where no
// dev server rewrites URLs. Paths come from the route id map in `routes.ts`.

import { createHashRouter, type RouteObject } from 'react-router-dom'
import { PageError, RouteError } from '../shell/AppError'
import { Call } from '../pages/Call'
import { Chat } from '../pages/Chat'
import { Companion } from '../pages/Companion'
import { Memory } from '../pages/Memory'
import { Settings } from '../pages/Settings'
import { Voices } from '../pages/Voices'
import { AppShell } from '../shell/AppShell'

// The design page, in development builds only. `import.meta.env.DEV` is a
// build-time constant, so a production build drops this branch, and with it the
// page's chunk.
function designRoutes(): RouteObject[] {
  if (!import.meta.env.DEV) return []
  return [
    {
      path: 'design',
      lazy: async () => ({ Component: (await import('../design/DesignSystem')).default }),
      errorElement: <RouteError />,
    },
  ]
}

export const appRoutes: RouteObject[] = [
  // Outside the Shell on purpose: the companion is a summoned panel, so it gets no
  // nav rail and none of the main window's chrome.
  { path: 'companion', element: <Companion />, errorElement: <RouteError /> },
  ...designRoutes(),
  {
    element: <AppShell />,
    // Without this, a render throw falls through to React Router's unstyled default
    // error page, showing the user a raw message.
    errorElement: <RouteError />,
    children: [
      {
        // A page that throws is replaced in place, inside the shell. Caught only at
        // the shell, it would take the providers down with it: a live call, the
        // chat socket, the tray's navigation.
        errorElement: <PageError />,
        children: [
          // Chat is the landing route, not Call: opening on the call screen would
          // suggest the mic is live before the user asked for it. Call stays one
          // click away.
          { index: true, element: <Chat /> },
          { path: 'chat', element: <Chat /> },
          { path: 'call', element: <Call /> },
          { path: 'voices', element: <Voices /> },
          { path: 'memory', element: <Memory /> },
          { path: 'settings', element: <Settings /> },
        ],
      },
    ],
  },
]

export const appRouter = createHashRouter(appRoutes)
