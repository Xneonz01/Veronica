// Short synthesized Web Audio tones for memory events. No sample files: each tone is
// an oscillator with a gain envelope. The three tones are named constants below so
// pitch, duration and gain are easy to tune by ear. They are quiet by default, and
// forgetting should never sound like an alarm.

interface Tone {
  notes: number[] // Hz, played in sequence
  noteMs: number // sounding length per note
  gapMs: number // gap between notes
  gain: number // peak gain — low; ambient, not attention-grabbing
  attackMs: number
  releaseMs: number
  type: OscillatorType
}

// --- Tunable tones ---
export const REMEMBERED: Tone = {
  notes: [659.25, 783.99], // E5 -> G5, a soft rising two-note
  noteMs: 90, gapMs: 24, gain: 0.06, attackMs: 8, releaseMs: 70, type: 'sine',
}
export const UPDATED: Tone = {
  notes: [440.0], // A4, a single neutral mid tick
  noteMs: 70, gapMs: 0, gain: 0.05, attackMs: 6, releaseMs: 60, type: 'sine',
}
export const FORGOTTEN: Tone = {
  notes: [392.0, 329.63], // G4 -> E4, a soft descending note (calm, not sad)
  noteMs: 95, gapMs: 24, gain: 0.05, attackMs: 8, releaseMs: 80, type: 'sine',
}

export type SoundAction = 'remembered' | 'updated' | 'forgotten'
const TONES: Record<SoundAction, Tone> = {
  remembered: REMEMBERED,
  updated: UPDATED,
  forgotten: FORGOTTEN,
}

let ctx: AudioContext | null = null
let enabled = true

function context(): AudioContext | null {
  try {
    if (ctx === null) ctx = new AudioContext()
    return ctx
  } catch {
    return null // Web Audio unavailable — sound is never the only signal
  }
}

export function setSoundsEnabled(on: boolean): void {
  enabled = on
}

export function playTone(tone: Tone): void {
  const ac = context()
  if (ac === null) return
  if (ac.state === 'suspended') void ac.resume() // resumes after the first user gesture
  let at = ac.currentTime
  for (const hz of tone.notes) {
    const osc = ac.createOscillator()
    const gain = ac.createGain()
    osc.type = tone.type
    osc.frequency.value = hz
    const end = at + (tone.noteMs + tone.releaseMs) / 1000
    gain.gain.setValueAtTime(0, at)
    gain.gain.linearRampToValueAtTime(tone.gain, at + tone.attackMs / 1000)
    gain.gain.linearRampToValueAtTime(0, end)
    osc.connect(gain).connect(ac.destination)
    osc.start(at)
    osc.stop(end + 0.02)
    at += (tone.noteMs + tone.gapMs) / 1000
  }
}

export function playAction(action: SoundAction): void {
  if (!enabled) return
  playTone(TONES[action])
}
