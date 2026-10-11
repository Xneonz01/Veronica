// The call orb's model, kept apart from the drawing so it can be tested: which of
// its moods each call state is, what each mood aims for, and how the drawing eases
// toward that aim frame by frame instead of jumping.

import type { CallState } from '../audio/useRealtimeCall'

/** How the orb carries itself, a step removed from the wire's call states. */
export type OrbMood = 'rest' | 'waking' | 'listening' | 'thinking' | 'speaking' | 'fault'

export function moodOf(state: CallState): OrbMood {
  switch (state) {
    case 'idle':
      return 'rest'
    case 'connecting':
    case 'warming_up':
      return 'waking'
    case 'error':
      return 'fault'
    default:
      return state
  }
}

export type Aim = {
  /** How lit the core is, 0 to 1. */
  light: number
  /** How far the glow reaches, 0 to 1. */
  glow: number
  /** How much the orb answers the sound it hears, 0 to 1. */
  voice: number
}

export const AIMS: Record<OrbMood, Aim> = {
  rest: { light: 0.55, glow: 0.25, voice: 0 },
  waking: { light: 0.7, glow: 0.4, voice: 0 },
  listening: { light: 0.8, glow: 0.45, voice: 1 },
  thinking: { light: 0.85, glow: 0.55, voice: 0 },
  speaking: { light: 1, glow: 0.75, voice: 1 },
  fault: { light: 0.4, glow: 0.1, voice: 0 },
}

/** One frame's step toward an aim: exponential, so it settles without overshoot. */
export function ease(current: Aim, target: Aim, rate: number): Aim {
  const step = (from: number, to: number) => from + (to - from) * rate
  return { light: step(current.light, target.light), glow: step(current.glow, target.glow), voice: step(current.voice, target.voice) }
}

/** Speaking rings: where ring `index` of `count` is at time `t` (seconds), 0 to 1. */
export function ringPhase(t: number, index: number, count: number, perSecond = 0.45): number {
  const phase = (t * perSecond + index / count) % 1
  return phase < 0 ? phase + 1 : phase
}
