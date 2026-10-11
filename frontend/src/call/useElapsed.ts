// The time since a moment, ticking once a second while there is one. Only the
// component that shows the clock re-renders.

import { useEffect, useState } from 'react'
import { elapsed } from './callCopy'

export function useElapsed(since: number | null): string | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (since === null) return
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [since])
  return since === null ? null : elapsed(now - since)
}
