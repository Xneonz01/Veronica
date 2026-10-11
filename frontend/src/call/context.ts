// The live call, held by the shell rather than the Call page, so a call carries
// on while you look at something else and the sidebar can show it.
//
// Two contexts, as for the chat: the whole call changes with every word she
// says; its status (in a call, which state, muted, since when) changes a few times
// a turn, and that is all the sidebar's dock reads.

import { createContext, useContext } from 'react'
import type { CallState, RealtimeCall } from '../audio/useRealtimeCall'

export interface CallStatus {
  state: CallState
  inCall: boolean
  muted: boolean
  /** When the current call began (Date.now()), or null outside one. */
  since: number | null
  /** Raised each time she writes to her memory during the call. */
  ripple: number
  startCall: () => Promise<void>
  endCall: () => void
  toggleMute: () => void
  /** Move a live call to another microphone or speakers. Outside a call they do
      nothing: the choice is already kept for the next one. */
  switchInput: (deviceId: string) => Promise<void>
  switchOutput: (deviceId: string) => Promise<void>
}

export const CallContext = createContext<RealtimeCall | null>(null)
export const CallStatusContext = createContext<CallStatus | null>(null)

export function useCall(): RealtimeCall {
  const call = useContext(CallContext)
  if (!call) throw new Error('useCall needs the CallProvider (in the app shell)')
  return call
}

export function useCallStatus(): CallStatus {
  const status = useContext(CallStatusContext)
  if (!status) throw new Error('useCallStatus needs the CallProvider (in the app shell)')
  return status
}
