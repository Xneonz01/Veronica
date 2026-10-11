// A small waveform drawn from a voice's id: the same voice always has the same
// shape, so voices are told apart at a glance.

/** Sixteen bar heights from the voice's id: the same voice, the same shape. */
export function waveOf(id: string): number[] {
  let hash = 2166136261
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619)
  return Array.from({ length: 16 }, (_, index) => {
    hash = Math.imul(hash ^ (hash >>> 13), 1274126177) + index
    return 0.25 + ((hash >>> 0) % 1000) / 1333
  })
}
