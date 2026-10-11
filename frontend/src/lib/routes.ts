// Route id -> path map. Kept dependency-free (no component imports) so both the
// router and the nav rail can import it without a circular dependency.

export const ROUTES = {
  chat: '/chat',
  call: '/call',
  voices: '/voices',
  memory: '/memory',
  settings: '/settings',
  companion: '/companion',
} as const
export type RouteId = keyof typeof ROUTES
