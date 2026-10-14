// A hands-free call: connect to /ws/voice, stream the microphone continuously, show
// live state and transcripts, play her voice, and stop it when the user talks over
// her. The chosen input and output devices apply, and can change mid-call.

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { WS } from '../lib/api'
import { onExpressiveChange, readExpressive, readGreeting } from '../notify/settings'
import {
  CALL_DROPPED,
  CONNECT_ERROR,
  MIC_GONE,
  MIC_LOST,
  MIC_SWITCH_FAILED,
  isMicProblem,
  micStartError,
  wireError,
  type CallError,
} from './callErrors'
import { AudioUnavailableError, MicPipeline } from './micPipeline'
import { PcmPlayer, reportPlayback } from './playback'
import { storedDevice } from './useAudioDevices'

export type { CallError, ErrorSeverity } from './callErrors'

const WS_URL = `${WS}/ws/voice`

export type CallState =
  | 'idle'
  | 'connecting'
  | 'warming_up'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'error'

// The states the server drives; 'idle' and 'error' are the client's own.
const SERVER_STATES = new Set<string>(['connecting', 'warming_up', 'listening', 'thinking', 'speaking'])

export interface CallMetrics {
  ttftMs: number | null
  firstAudioMs: number | null
  totalMs: number | null
}

// Analyser taps the orb reads each frame (via ref, never React state).
export interface AmplitudeTaps {
  mic: AnalyserNode | null
  playback: AnalyserNode | null
}

interface ServerMessage {
  type: string
  state?: string
  text?: string
  kind?: string
  model?: unknown
  models?: unknown
  free_mib?: unknown
  total_mib?: unknown
  needs_mib?: unknown
  ttft_ms?: number | null
  first_audio_ms?: number | null
  total_ms?: number | null
  reply?: number
}

export interface RealtimeCall {
  state: CallState
  partial: string
  finalText: string
  reply: string
  /** What her reply said she did and she didn't, shown apart from her words. */
  caveat: string
  metrics: CallMetrics | null
  error: CallError | null
  inCall: boolean
  muted: boolean
  amplitude: RefObject<AmplitudeTaps>
  startCall: () => Promise<void>
  endCall: () => void
  toggleMute: () => void
  switchInput: (deviceId: string) => Promise<void>
  switchOutput: (sinkId: string) => Promise<void>
}

export function useRealtimeCall(): RealtimeCall {
  const [state, setState] = useState<CallState>('idle')
  const [partial, setPartial] = useState('')
  const [finalText, setFinalText] = useState('')
  const [reply, setReply] = useState('')
  const [caveat, setCaveat] = useState('')
  const [metrics, setMetrics] = useState<CallMetrics | null>(null)
  const [error, setError] = useState<CallError | null>(null)
  const [inCall, setInCall] = useState(false)
  const [muted, setMuted] = useState(false)
  const [playing, setPlaying] = useState(false)

  const ws = useRef<WebSocket | null>(null)
  const player = useRef<PcmPlayer | null>(null)
  const mic = useRef<MicPipeline | null>(null)
  const stopReports = useRef<(() => void) | null>(null)
  const mutedNow = useRef(false) // read by device switches that finish later
  const starting = useRef(false) // guards a double-click during the connect window
  const amplitude = useRef<AmplitudeTaps>({ mic: null, playback: null })

  const teardown = useCallback(() => {
    // Retire the call first: the socket's close handler checks whether its socket
    // is still the live one, and must find that it isn't.
    const [lastMic, lastPlayer, lastSocket] = [mic.current, player.current, ws.current]
    mic.current = null
    player.current = null
    ws.current = null
    amplitude.current = { mic: null, playback: null }
    stopReports.current?.()
    stopReports.current = null
    lastMic?.close()
    lastPlayer?.close()
    lastSocket?.close()
  }, [])

  const failFatal = useCallback(
    (err: CallError) => {
      setError(err)
      setState('error')
      teardown()
      setInCall(false)
    },
    [teardown],
  )

  const onMicEnded = useCallback(() => {
    // The microphone in use was unplugged: fall back to the system default.
    const current = mic.current
    if (!current) return
    void current.switchTo('').then((result) => {
      if (result === 'switched') {
        amplitude.current.mic = current.analyser
        setError(MIC_LOST)
      } else if (result === 'failed') {
        setError(MIC_GONE)
      }
    })
  }, [])

  const switchInput = useCallback(async (deviceId: string) => {
    const current = mic.current
    if (!current) return // not in a call; the choice is kept for the next one
    const result = await current.switchTo(deviceId)
    if (result === 'switched') {
      amplitude.current.mic = current.analyser
      setError((cur) => (cur && isMicProblem(cur) ? null : cur))
    } else if (result === 'failed') {
      setError(MIC_SWITCH_FAILED)
    }
  }, [])

  const switchOutput = useCallback(async (sinkId: string) => {
    await player.current?.setSinkId(sinkId)
  }, [])

  const onMessage = useCallback(
    (event: MessageEvent<string | ArrayBuffer>) => {
      if (typeof event.data !== 'string') {
        player.current?.enqueue(event.data)
        return
      }
      let msg: ServerMessage
      try {
        msg = JSON.parse(event.data) as ServerMessage
      } catch {
        console.warn('Dropped a malformed WS frame')
        return
      }
      switch (msg.type) {
        case 'veronica_state':
          if (msg.state && SERVER_STATES.has(msg.state)) setState(msg.state as CallState)
          break
        case 'asr_partial':
          // She stops the moment the user talks. While her voice is still being made
          // the server does this (a barge-in); the audio left playing after that is
          // only the client's to stop.
          if (player.current?.playing) player.current.flush()
          setPartial(msg.text ?? '')
          break
        case 'asr_final':
          setFinalText(msg.text ?? '')
          setPartial('')
          setReply('')
          setCaveat('')
          // The user has spoken again, so a problem with the last turn is behind them.
          // Not on "listening": the server sends that straight after the error itself.
          setError((cur) => (cur?.severity === 'recoverable' ? null : cur))
          break
        case 'assistant_token':
          setReply((current) => current + (msg.text ?? ''))
          break
        case 'assistant_caveat':
          setCaveat(msg.text ?? '')
          break
        case 'playback_flush':
          player.current?.flush()
          break
        case 'reply_audio':
          if (typeof msg.reply === 'number') player.current?.beginReply(msg.reply)
          break
        case 'metrics':
          setMetrics({
            ttftMs: msg.ttft_ms ?? null,
            firstAudioMs: msg.first_audio_ms ?? null,
            totalMs: msg.total_ms ?? null,
          })
          break
        case 'error': {
          const err = wireError(msg.kind, msg)
          if (err.severity === 'fatal') failFatal(err)
          else setError(err)
          break
        }
        default:
          break
      }
    },
    [failFatal],
  )

  const startCall = useCallback(async () => {
    // `inCall` only turns true once the mic is live, so a second click during the
    // connect window would otherwise open a second socket and microphone.
    if (starting.current || ws.current) return
    starting.current = true
    setState('connecting')
    setError(null)
    setReply('')
    setCaveat('')
    setPartial('')
    setFinalText('')
    setMetrics(null)
    setMuted(false)
    mutedNow.current = false

    const socket = new WebSocket(WS_URL)
    socket.binaryType = 'arraybuffer'
    const voice = new PcmPlayer()
    voice.onPlayingChange = setPlaying
    ws.current = socket
    player.current = voice
    socket.onmessage = onMessage
    // End Call, or a failure, during any wait below retires this socket; whatever
    // opens after that is let go instead of wired into a call that has ended.
    const live = () => ws.current === socket

    try {
      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve()
        socket.onerror = () => reject(new Error('connect_failed'))
        socket.onclose = () => reject(new Error('connect_failed'))
      })
      if (!live()) return
      socket.onerror = null // a close always follows, and says what happened
      socket.onclose = () => {
        // End Call and a failure retire the socket before closing it, so a close
        // that gets here is the backend going away mid-call.
        if (live()) failFatal(CALL_DROPPED)
      }

      await voice.resume()
      if (!live()) return
      amplitude.current.playback = voice.getAnalyser()
      const out = storedDevice.output()
      if (out) await voice.setSinkId(out)
      if (!live()) return

      let pipeline: MicPipeline
      try {
        const send = (frame: ArrayBuffer) => {
          if (socket.readyState === WebSocket.OPEN) socket.send(frame)
        }
        pipeline = await MicPipeline.open(storedDevice.input(), send, onMicEnded)
      } catch (err) {
        if (live()) failFatal(err instanceof AudioUnavailableError ? CONNECT_ERROR : micStartError(err))
        return
      }
      if (!live()) {
        pipeline.close()
        return
      }
      mic.current = pipeline
      amplitude.current.mic = pipeline.analyser
      stopReports.current = reportPlayback(voice, (position) => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ action: 'playback_position', ...position }))
        }
      })
      socket.send(
        JSON.stringify({ action: 'call_start', greeting: readGreeting(), expressive: readExpressive() }),
      )
      setInCall(true)
      // Stay on 'connecting': the backend drives 'warming_up' -> 'listening'.
    } catch {
      if (live()) failFatal(CONNECT_ERROR)
    } finally {
      starting.current = false
    }
  }, [onMessage, failFatal, onMicEnded])

  const endCall = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ action: 'call_stop' }))
    }
    teardown()
    setInCall(false)
    setMuted(false)
    mutedNow.current = false
    setState('idle')
    // A passing problem speaks of the call in progress ("I may be slow to answer"), so
    // it ends with the call.
    setError((cur) => (cur?.severity === 'recoverable' ? null : cur))
  }, [teardown])

  const toggleMute = useCallback(() => {
    const next = !mutedNow.current
    mutedNow.current = next
    mic.current?.setMuted(next)
    setMuted(next)
  }, [])

  useEffect(() => teardown, [teardown])

  // Expressive voice switched during a call: from her next reply on.
  useEffect(
    () =>
      onExpressiveChange((expressive) => {
        if (ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ action: 'call_options', expressive }))
        }
      }),
    [],
  )

  return {
    // The backend says "listening" once the last of her words is synthesised, and
    // the audio can play for seconds after that: until it has, she is speaking.
    state: state === 'listening' && playing ? 'speaking' : state,
    partial,
    finalText,
    reply,
    caveat,
    metrics,
    error,
    inCall,
    muted,
    amplitude,
    startCall,
    endCall,
    toggleMute,
    switchInput,
    switchOutput,
  }
}
