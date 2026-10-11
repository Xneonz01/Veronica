// Her presence during a call: a luminous core in her colour, echoing her mark.
// It rests and breathes when idle; a ring follows your voice while she listens;
// a slow arc circles while she thinks; rings leave her as she speaks, as loud as
// she is. A memory she writes sends one ripple out.
//
// Drawn on a canvas in an animation-frame loop that reads loudness straight from
// the audio nodes, so a frame never re-renders React. Colours are read from the
// tokens where the orb sits, so it follows the theme and any theme island.
// Under reduced motion it draws one still frame per state.

import { useEffect, useRef, type RefObject } from 'react'
import type { AmplitudeTaps, CallState } from '../audio/useRealtimeCall'
import { readRms } from '../audio/useAmplitude'
import { parseColor, type Rgba } from '../lib/contrast'
import { AIMS, ease, moodOf, ringPhase, type Aim } from './orbModel'
import styles from './Orb.module.css'

type Palette = { her: Rgba; you: Rgba; fault: Rgba }

const RINGS = 3
// A highlight is light in either theme; the ink colour would darken it in light.
const WHITE: Rgba = { r: 1, g: 1, b: 1, alpha: 1 }
const RIPPLE_MS = 900

export function Orb({
  state,
  amplitude,
  size = 280,
  ripple = 0,
}: {
  state: CallState
  amplitude: RefObject<AmplitudeTaps>
  size?: number
  /** Raise it to send one ripple out: a memory she just wrote. */
  ripple?: number
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const probe = useRef<HTMLSpanElement>(null)
  const live = useRef({ state, rippleAt: -Infinity })
  // Where the orb has eased to so far. It outlives the drawing loop, which
  // restarts on a state change and must carry on from here rather than jump.
  const eased = useRef<Aim | null>(null)

  useEffect(() => {
    live.current.state = state
  }, [state])

  useEffect(() => {
    if (ripple > 0) live.current.rippleAt = performance.now()
  }, [ripple])

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context || !probe.current) return
    const probeElement = probe.current
    const scale = Math.min(window.devicePixelRatio || 1, 2)
    element.width = size * scale
    element.height = size * scale
    context.scale(scale, scale)

    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const buffer = new Float32Array(1024)
    let palette = readPalette(probeElement)
    let aim: Aim = eased.current ?? AIMS[moodOf(live.current.state)]
    let loud = 0
    let frame = 0
    let count = 0

    const draw = (now: number) => {
      // The theme can change under a running call; a colour read a second is plenty.
      if (count++ % 60 === 0) palette = readPalette(probeElement)
      const mood = moodOf(live.current.state)
      aim = still ? AIMS[mood] : ease(aim, AIMS[mood], 0.08)
      eased.current = aim
      const taps = amplitude.current
      const heard = mood === 'listening' ? readRms(taps.mic, buffer) * 6 : mood === 'speaking' ? readRms(taps.playback, buffer) * 5 : 0
      loud += (Math.min(1, heard) * aim.voice - loud) * 0.25
      paint(context, size, palette, mood, aim, still ? 0 : loud, still ? 0 : now / 1000, now - live.current.rippleAt)
      if (!still) frame = requestAnimationFrame(draw)
    }

    if (still) draw(0)
    else frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [size, amplitude, state])

  return (
    <span className={styles.orb} style={{ width: size, height: size }} aria-hidden="true">
      <canvas ref={canvas} style={{ width: size, height: size }} />
      <span ref={probe} className={styles.probe} />
    </span>
  )
}

/** The tokens as the orb's own place in the page resolves them. */
function readPalette(probe: HTMLElement): Palette {
  const read = (token: string, fallback: Rgba) => {
    probe.style.color = `var(${token})`
    return parseColor(getComputedStyle(probe).color) ?? fallback
  }
  return {
    her: read('--assistant', { r: 0.2, g: 0.8, b: 0.55, alpha: 1 }),
    you: read('--foreground', { r: 0.9, g: 0.9, b: 0.9, alpha: 1 }),
    fault: read('--destructive', { r: 0.9, g: 0.3, b: 0.3, alpha: 1 }),
  }
}

function rgba(color: Rgba, alpha: number): string {
  const channel = (value: number) => Math.round(Math.min(1, Math.max(0, value)) * 255)
  return `rgba(${channel(color.r)}, ${channel(color.g)}, ${channel(color.b)}, ${Math.max(0, alpha)})`
}

function paint(
  context: CanvasRenderingContext2D,
  size: number,
  palette: Palette,
  mood: ReturnType<typeof moodOf>,
  aim: Aim,
  loud: number,
  t: number,
  sinceRipple: number,
) {
  const centre = size / 2
  const base = size * 0.17
  const color = mood === 'fault' ? palette.fault : palette.her
  context.clearRect(0, 0, size, size)

  // The glow, reaching further as she is more present.
  const breath = mood === 'rest' || mood === 'waking' ? 0.5 + 0.5 * Math.sin(t * (mood === 'waking' ? 2.2 : 1.1)) : 0
  const reach = base * (1.6 + aim.glow * 1.3 + loud * 0.6)
  const glow = context.createRadialGradient(centre, centre, base * 0.6, centre, centre, reach)
  glow.addColorStop(0, rgba(color, 0.32 * aim.glow + 0.08 * breath))
  glow.addColorStop(1, rgba(color, 0))
  context.fillStyle = glow
  context.beginPath()
  context.arc(centre, centre, reach, 0, Math.PI * 2)
  context.fill()

  // Her mark's ring, always there, faint.
  context.lineWidth = 1
  context.strokeStyle = rgba(color, 0.18)
  context.beginPath()
  context.arc(centre, centre, base * 1.9, 0, Math.PI * 2)
  context.stroke()

  if (mood === 'speaking') {
    for (let index = 0; index < RINGS; index += 1) {
      const phase = ringPhase(t, index, RINGS)
      context.lineWidth = 1.5
      context.strokeStyle = rgba(color, (1 - phase) * (0.18 + loud * 0.55))
      context.beginPath()
      context.arc(centre, centre, base * (1.15 + phase * 1.6), 0, Math.PI * 2)
      context.stroke()
    }
  }

  if (mood === 'listening') {
    // Your voice, in your colour rather than hers.
    context.lineWidth = 2
    context.strokeStyle = rgba(palette.you, 0.25 + loud * 0.5)
    context.beginPath()
    context.arc(centre, centre, base * (1.28 + loud * 0.55), 0, Math.PI * 2)
    context.stroke()
  }

  if (mood === 'thinking') {
    const start = t * 2.4
    context.lineWidth = 2
    context.lineCap = 'round'
    context.strokeStyle = rgba(color, 0.75)
    context.beginPath()
    context.arc(centre, centre, base * 1.42, start, start + Math.PI * 0.45)
    context.stroke()
  }

  if (sinceRipple >= 0 && sinceRipple < RIPPLE_MS) {
    const progress = sinceRipple / RIPPLE_MS
    context.lineWidth = 2
    context.strokeStyle = rgba(palette.her, (1 - progress) * 0.7)
    context.beginPath()
    context.arc(centre, centre, base * (1.2 + progress * 2.2), 0, Math.PI * 2)
    context.stroke()
  }

  // The core: lit from the upper left, like the orb in her mark.
  const radius = base * (1 + loud * 0.08 + breath * 0.02)
  const core = context.createRadialGradient(centre - radius * 0.35, centre - radius * 0.4, radius * 0.1, centre, centre, radius)
  core.addColorStop(0, rgba(mix(color, WHITE, 0.35), 0.55 + 0.45 * aim.light))
  core.addColorStop(1, rgba(color, 0.35 + 0.65 * aim.light))
  context.fillStyle = core
  context.beginPath()
  context.arc(centre, centre, radius, 0, Math.PI * 2)
  context.fill()
}

function mix(a: Rgba, b: Rgba, amount: number): Rgba {
  return { r: a.r + (b.r - a.r) * amount, g: a.g + (b.g - a.g) * amount, b: a.b + (b.b - a.b) * amount, alpha: 1 }
}
