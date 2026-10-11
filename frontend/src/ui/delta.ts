export type DeltaDirection = 'up' | 'down' | 'flat'

/** A leading + reads as up, a minus (− or -) as down, anything else as flat. */
export function deltaDirection(delta: string): DeltaDirection {
  if (delta.startsWith('+')) return 'up'
  if (delta.startsWith('−') || delta.startsWith('-')) return 'down'
  return 'flat'
}
