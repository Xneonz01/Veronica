// What the call says about itself, in one place: the state under her orb and the
// time on the call.

import type { CallState } from '../audio/useRealtimeCall'

export const STATE_LABEL: Record<CallState, string> = {
  idle: 'Ready when you are',
  connecting: 'Connecting',
  warming_up: 'Waking up her voice',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
  error: 'The call ended',
}

/** Time on a call as a clock reads it: 0:42, 12:05, 1:03:09. */
export function elapsed(milliseconds: number): string {
  const total = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = String(total % 60).padStart(2, '0')
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`
}
