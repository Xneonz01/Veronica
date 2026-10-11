// In-app voice recording: reuse the capture worklet (posts 16 kHz PCM16 frames),
// buffer them, and encode a WAV on stop. Exposes an analyser for a live level
// meter. Records through the input device chosen in the picker (same path as a call).

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { acquireMic } from './micCapture'
import { storedDevice } from './useAudioDevices'

const RECORD_SR = 16000

function encodeWav(chunks: Int16Array[], sampleRate: number): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0)
  const samples = new Int16Array(total)
  let offset = 0
  for (const chunk of chunks) {
    samples.set(chunk, offset)
    offset += chunk.length
  }
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const str = (at: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i))
  }
  str(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  str(8, 'WAVE')
  str(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  str(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  new Int16Array(buffer, 44).set(samples)
  return new Blob([buffer], { type: 'audio/wav' })
}

export interface Recorder {
  recording: boolean
  seconds: number
  analyser: RefObject<AnalyserNode | null>
  start: () => Promise<void>
  stop: () => Blob | null
  /** Stop and keep nothing, including a start still waiting for the microphone. */
  cancel: () => void
}

export function useRecorder(): Recorder {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const ctx = useRef<AudioContext | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const node = useRef<AudioWorkletNode | null>(null)
  const analyser = useRef<AnalyserNode | null>(null)
  const chunks = useRef<Int16Array[]>([])
  const timer = useRef<number | null>(null)

  const teardown = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current)
    timer.current = null
    node.current?.disconnect()
    stream.current?.getTracks().forEach((track) => track.stop())
    // Closing twice rejects; the first close is the one that matters.
    ctx.current?.close().catch(() => undefined)
    node.current = null
    stream.current = null
    ctx.current = null
    analyser.current = null
  }, [])

  const start = useCallback(async () => {
    teardown() // a second start never leaves the first one running
    chunks.current = []
    const audioCtx = new AudioContext()
    ctx.current = audioCtx
    try {
      await audioCtx.audioWorklet.addModule('/audio/capture-processor.js')
      const worklet = new AudioWorkletNode(audioCtx, 'capture-processor')
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        chunks.current.push(new Int16Array(event.data))
      }
      node.current = worklet
      // Record through the device chosen in the picker (falling back to the system
      // default if it's gone), the same capture path a call uses.
      const cap = await acquireMic(audioCtx, storedDevice.input(), worklet, () => {})
      // Stopped, or the sheet closed, while the microphone was being asked for.
      if (ctx.current !== audioCtx) {
        cap.stream.getTracks().forEach((track) => track.stop())
        return
      }
      stream.current = cap.stream
      analyser.current = cap.analyser
    } catch (err) {
      // A refused or missing microphone must not leave the audio context running.
      if (ctx.current === audioCtx) teardown()
      else audioCtx.close().catch(() => undefined)
      throw err
    }

    setSeconds(0)
    setRecording(true)
    const started = performance.now()
    timer.current = window.setInterval(() => setSeconds((performance.now() - started) / 1000), 200)
  }, [teardown])

  const stop = useCallback((): Blob | null => {
    teardown()
    setRecording(false)
    if (chunks.current.length === 0) return null
    return encodeWav(chunks.current, RECORD_SR)
  }, [teardown])

  const cancel = useCallback(() => {
    teardown()
    chunks.current = []
    setRecording(false)
  }, [teardown])

  // Release the mic if the component unmounts mid-recording.
  useEffect(() => () => teardown(), [teardown])

  return { recording, seconds, analyser, start, stop, cancel }
}
