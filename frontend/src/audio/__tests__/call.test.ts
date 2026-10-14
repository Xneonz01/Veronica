// @vitest-environment jsdom
// A call's edges: an error that has to outlive the "listening" sent after it, her
// voice stopping when the user talks over its tail, End Call while connecting, the
// backend going away mid-call, and microphones that won't open or disappear.

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SwitchResult } from '../micPipeline'
import { writeExpressive } from '../../notify/settings'
import { useRealtimeCall } from '../useRealtimeCall'

const pipeline = vi.hoisted(() => ({ open: vi.fn() }))
const voice = vi.hoisted(() => ({ playing: false, flush: vi.fn(), beginReply: vi.fn() }))
const reports = vi.hoisted(() => ({
  send: null as ((position: { reply: number; samples: number }) => void) | null,
  stop: vi.fn(),
}))

vi.mock('../micPipeline', () => ({
  AudioUnavailableError: class extends Error {},
  MicPipeline: { open: pipeline.open },
}))

vi.mock('../playback', () => ({
  reportPlayback: (_player: unknown, send: (position: { reply: number; samples: number }) => void) => {
    reports.send = send
    return reports.stop
  },
  PcmPlayer: class {
    onPlayingChange = null
    get playing() {
      return voice.playing
    }
    flush() {
      voice.flush()
    }
    beginReply(id: number) {
      voice.beginReply(id)
    }
    resume() {
      return Promise.resolve()
    }
    getAnalyser() {
      return null
    }
    setSinkId() {
      return Promise.resolve()
    }
    enqueue() {}
    close() {}
  },
}))

class FakeSocket {
  static OPEN = 1
  static last: FakeSocket | null = null
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor() {
    FakeSocket.last = this
  }

  connect() {
    this.readyState = 1
    this.onopen?.()
  }

  send(data: unknown) {
    this.sent.push(data)
  }

  emit(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }
}

function socket(): FakeSocket {
  if (!FakeSocket.last) throw new Error('no socket yet')
  return FakeSocket.last
}

function fakeMic(switchResult: SwitchResult = 'switched') {
  return {
    analyser: {} as AnalyserNode,
    close: vi.fn(),
    setMuted: vi.fn(),
    switchTo: vi.fn(async () => switchResult),
  }
}

async function connected(mic = fakeMic()) {
  pipeline.open.mockResolvedValue(mic)
  const hook = renderHook(() => useRealtimeCall())
  let started: Promise<void> = Promise.resolve()
  act(() => {
    started = hook.result.current.startCall()
  })
  await act(async () => {
    socket().connect()
    await started
  })
  return { ...hook, mic }
}

beforeEach(() => {
  FakeSocket.last = null
  reports.send = null
  voice.playing = false
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('a call', () => {
  it('keeps a reply error on screen until the user speaks again', async () => {
    const { result } = await connected()
    expect(result.current.inCall).toBe(true)
    act(() => socket().emit({ type: 'error', kind: 'llm_unreachable' }))
    act(() => socket().emit({ type: 'veronica_state', state: 'listening' }))
    expect(result.current.error?.kind).toBe('llm_unreachable')
    act(() => socket().emit({ type: 'asr_final', text: 'Are you there?' }))
    expect(result.current.error).toBeNull()
  })

  it('keeps what she said she did and did not apart from her words, until you speak', async () => {
    const { result } = await connected()
    act(() => socket().emit({ type: 'assistant_token', text: "I've forgotten that." }))
    act(() => socket().emit({ type: 'assistant_caveat', text: "My memory didn't actually change just now." }))
    expect(result.current.reply).toBe("I've forgotten that.")
    expect(result.current.caveat).toBe("My memory didn't actually change just now.")
    act(() => socket().emit({ type: 'asr_final', text: 'Please do it then.' }))
    expect(result.current.caveat).toBe('')
  })

  it('stops her voice when the user talks over what is still playing', async () => {
    await connected()
    act(() => socket().emit({ type: 'asr_partial', text: 'so' }))
    expect(voice.flush).not.toHaveBeenCalled()
    voice.playing = true
    act(() => socket().emit({ type: 'asr_partial', text: 'wait' }))
    expect(voice.flush).toHaveBeenCalledTimes(1)
  })

  it('tells the server how much of each reply has played, until the call ends', async () => {
    const { result } = await connected()
    act(() => socket().emit({ type: 'reply_audio', reply: 4 }))
    expect(voice.beginReply).toHaveBeenCalledWith(4)
    reports.send?.({ reply: 4, samples: 1200 })
    expect(socket().sent).toContain(JSON.stringify({ action: 'playback_position', reply: 4, samples: 1200 }))
    act(() => result.current.endCall())
    expect(reports.stop).toHaveBeenCalled()
  })

  it('ends a call that cannot start for a missing model, naming it (bug 44)', async () => {
    const { result } = await connected()
    act(() => socket().emit({ type: 'error', kind: 'models_missing', models: 'stt' }))
    expect(result.current.error?.message).toContain("speech recognition isn't installed")
    expect(result.current.inCall).toBe(false)
  })

  it('ends a call the graphics card has no room for, saying how much (bug 46)', async () => {
    const { result } = await connected()
    act(() => socket().emit({ type: 'error', kind: 'card_full', free_mib: '4538', needs_mib: '5146' }))
    expect(result.current.error?.message.replaceAll(' ', ' ')).toContain('A call needs 5.1 GB of it free, and 4.4 GB is.')
    expect(result.current.inCall).toBe(false)
  })

  it('keeps going through an error it has no words for', async () => {
    const { result } = await connected()
    act(() => socket().emit({ type: 'error', kind: 'something_new' }))
    expect(result.current.inCall).toBe(true)
    expect(result.current.error?.severity).toBe('recoverable')
  })

  it('says so when the backend goes away mid-call', async () => {
    const { result, mic } = await connected()
    act(() => socket().close())
    expect(result.current.error?.kind).toBe('call_dropped')
    expect(result.current.inCall).toBe(false)
    expect(mic.close).toHaveBeenCalled()
  })

  it('ends quietly on End Call', async () => {
    const { result, mic } = await connected()
    act(() => result.current.endCall())
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
    expect(mic.close).toHaveBeenCalledTimes(1)
  })

  it('takes a passing problem away with the call it spoke of', async () => {
    // Seen live: "I may be slow to answer" stayed on the screen after End Call.
    const { result } = await connected()
    act(() => socket().emit({ type: 'error', kind: 'card_tight' }))
    expect(result.current.error?.kind).toBe('card_tight')
    act(() => result.current.endCall())
    expect(result.current.error).toBeNull()
  })
})

describe('expressive voice', () => {
  const options = (sent: unknown[]) =>
    sent.filter((frame) => typeof frame === 'string' && frame.includes('call_options'))

  afterEach(() => writeExpressive(true))

  it('goes with the call when it starts, as it is set', async () => {
    writeExpressive(false)
    await connected()
    const start = socket().sent.find((frame) => typeof frame === 'string' && frame.includes('call_start'))
    expect(JSON.parse(start as string)).toMatchObject({ action: 'call_start', expressive: false })
  })

  it('reaches a call in progress when it is switched', async () => {
    await connected()
    act(() => writeExpressive(false))
    act(() => writeExpressive(true))
    expect(options(socket().sent)).toEqual([
      JSON.stringify({ action: 'call_options', expressive: false }),
      JSON.stringify({ action: 'call_options', expressive: true }),
    ])
  })

  it('waits for the call to connect, which takes the setting with it', async () => {
    pipeline.open.mockResolvedValue(fakeMic())
    const { result } = renderHook(() => useRealtimeCall())
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = result.current.startCall()
    })
    act(() => writeExpressive(false)) // the socket is there, not yet open
    expect(options(socket().sent)).toEqual([])
    await act(async () => {
      socket().connect()
      await started
    })
    const start = socket().sent.find((frame) => typeof frame === 'string' && frame.includes('call_start'))
    expect(JSON.parse(start as string)).toMatchObject({ expressive: false })
  })

  it('sends nothing once the call has ended', async () => {
    const { result } = await connected()
    const sock = socket()
    act(() => result.current.endCall())
    act(() => writeExpressive(false))
    expect(options(sock.sent)).toEqual([])
  })
})

describe('ending a call before it has started', () => {
  it('opens no microphone and shows no error', async () => {
    const { result } = renderHook(() => useRealtimeCall())
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = result.current.startCall()
    })
    act(() => result.current.endCall())
    await act(async () => {
      await started
    })
    expect(pipeline.open).not.toHaveBeenCalled()
    expect(result.current.error).toBeNull()
    expect(result.current.state).toBe('idle')
  })

  it('lets go of a microphone that opens after the call ended', async () => {
    const mic = fakeMic()
    let resolveMic: (value: typeof mic) => void = () => undefined
    pipeline.open.mockReturnValue(new Promise((resolve) => (resolveMic = resolve)))
    const { result } = renderHook(() => useRealtimeCall())
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = result.current.startCall()
    })
    act(() => socket().connect())
    await vi.waitFor(() => expect(pipeline.open).toHaveBeenCalled())
    act(() => result.current.endCall())
    await act(async () => {
      resolveMic(mic)
      await started
    })
    expect(mic.close).toHaveBeenCalled()
    expect(result.current.inCall).toBe(false)
  })
})

describe("a microphone that won't cooperate", () => {
  it.each([
    ['NotFoundError', 'mic_missing'],
    ['NotReadableError', 'mic_busy'],
    ['NotAllowedError', 'mic_denied'],
  ])('at the start of a call (%s) is named as the microphone', async (name, kind) => {
    pipeline.open.mockRejectedValue(new DOMException('no', name))
    const { result } = renderHook(() => useRealtimeCall())
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = result.current.startCall()
    })
    await act(async () => {
      socket().connect()
      await started
    })
    expect(result.current.error?.kind).toBe(kind)
  })

  it('unplugged, with no other to fall back on, says there is none', async () => {
    const { result } = await connected(fakeMic('failed'))
    const onEnded = pipeline.open.mock.calls[0][2] as () => void
    await act(async () => onEnded())
    expect(result.current.error?.kind).toBe('mic_gone')
    expect(result.current.inCall).toBe(true)
  })

  it('refusing a switch says so, and a working switch clears it', async () => {
    const mic = fakeMic('failed')
    const { result } = await connected(mic)
    await act(async () => result.current.switchInput('usb-mic'))
    expect(result.current.error?.kind).toBe('mic_switch_failed')
    mic.switchTo.mockResolvedValue('switched')
    await act(async () => result.current.switchInput('desk-mic'))
    expect(result.current.error).toBeNull()
  })

  it('mutes the microphone it has, not a stale one', async () => {
    const { result, mic } = await connected()
    act(() => result.current.toggleMute())
    act(() => result.current.toggleMute())
    expect(mic.setMuted.mock.calls).toEqual([[true], [false]])
    expect(result.current.muted).toBe(false)
  })
})
