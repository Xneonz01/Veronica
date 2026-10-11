// Owns the live call for the whole window (see context.ts).

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useRealtimeCall } from '../audio/useRealtimeCall'
import { useNotifications } from '../notify/useNotifications'
import { CallContext, CallStatusContext, type CallStatus } from './context'

export function CallProvider({ children }: { children: ReactNode }) {
  const call = useRealtimeCall()
  const { latest } = useNotifications()
  const [since, setSince] = useState<number | null>(null)
  const [ripple, setRipple] = useState(0)
  const { state, inCall, muted, startCall, endCall, toggleMute, switchInput, switchOutput } = call

  useEffect(() => {
    setSince(inCall ? Date.now() : null)
  }, [inCall])

  // A memory written during a call ripples out of her orb. One from before the call
  // is already seen, so starting a call doesn't ripple for it.
  const seen = useRef(latest)
  useEffect(() => {
    if (latest === seen.current) return
    seen.current = latest
    if (latest && inCall) setRipple((count) => count + 1)
  }, [latest, inCall])

  const status = useMemo<CallStatus>(
    () => ({ state, inCall, muted, since, ripple, startCall, endCall, toggleMute, switchInput, switchOutput }),
    [state, inCall, muted, since, ripple, startCall, endCall, toggleMute, switchInput, switchOutput],
  )

  return (
    <CallContext.Provider value={call}>
      <CallStatusContext.Provider value={status}>{children}</CallStatusContext.Provider>
    </CallContext.Provider>
  )
}
