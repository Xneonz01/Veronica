// The player's one signal to the call screen: her voice started, and it finished
// (or was cut off). The call shows "speaking" from it until the audio has drained.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PcmPlayer, reportPlayback } from '../playback'

class FakeSource {
  buffer: unknown = null
  onended: (() => void) | null = null
  connect() {}
  start() {}
  stop() {}
}

class FakeContext {
  static last: FakeContext | null = null
  currentTime = 0
  destination = {}
  sources: FakeSource[] = []
  constructor() {
    FakeContext.last = this
  }
  createAnalyser() {
    return { fftSize: 0, connect() {} }
  }
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, getChannelData: () => new Float32Array(length) }
  }
  createBufferSource() {
    const source = new FakeSource()
    this.sources.push(source)
    return source
  }
  resume() {
    return Promise.resolve()
  }
  close() {
    return Promise.resolve()
  }
}

const chunk = () => new Int16Array(2400).buffer

beforeEach(() => {
  vi.stubGlobal('AudioContext', FakeContext)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the player', () => {
  it('says once that she started, and once that the last of it has played', () => {
    const player = new PcmPlayer()
    const changes: boolean[] = []
    player.onPlayingChange = (playing) => changes.push(playing)
    player.enqueue(chunk())
    player.enqueue(chunk())
    expect(changes).toEqual([true])
    const [first, second] = FakeContext.last?.sources ?? []
    first.onended?.()
    expect(changes).toEqual([true])
    second.onended?.()
    expect(changes).toEqual([true, false])
  })

  it('says she stopped when she is cut off, and nothing when she was quiet already', () => {
    const player = new PcmPlayer()
    const changes: boolean[] = []
    player.onPlayingChange = (playing) => changes.push(playing)
    player.flush()
    expect(changes).toEqual([])
    player.enqueue(chunk())
    player.flush()
    expect(changes).toEqual([true, false])
  })
})

// 2400 samples at 24 kHz: each chunk plays for 0.1 s.
describe('how much of a reply has played', () => {
  it('counts the current reply by the audio clock, part of a chunk included', () => {
    const player = new PcmPlayer()
    player.enqueue(chunk()) // before any reply's mark: not counted
    const clock = FakeContext.last as FakeContext
    clock.currentTime = 0.1
    player.beginReply(7)
    player.enqueue(chunk()) // plays 0.1 to 0.2
    player.enqueue(chunk()) // plays 0.2 to 0.3
    expect(player.replyPosition()).toEqual({ reply: 7, samples: 0 })
    clock.currentTime = 0.25
    expect(player.replyPosition()).toEqual({ reply: 7, samples: 3600 })
    clock.currentTime = 1
    expect(player.replyPosition()).toEqual({ reply: 7, samples: 4800 })
  })

  it('has no reply once she is cut off, so stopped audio never counts as heard', () => {
    const player = new PcmPlayer()
    player.beginReply(3)
    player.enqueue(chunk())
    player.flush()
    expect(player.replyPosition()).toBeNull()
  })

  it('tells the server every 100 ms, and only when the position has moved', () => {
    vi.useFakeTimers()
    try {
      const player = new PcmPlayer()
      const sent: unknown[] = []
      const stop = reportPlayback(player, (position) => sent.push(position))
      player.beginReply(1)
      player.enqueue(chunk())
      const clock = FakeContext.last as FakeContext
      vi.advanceTimersByTime(100)
      expect(sent).toEqual([{ reply: 1, samples: 0 }])
      vi.advanceTimersByTime(300) // nothing has played since
      expect(sent).toHaveLength(1)
      clock.currentTime = 0.05
      vi.advanceTimersByTime(100)
      expect(sent).toEqual([
        { reply: 1, samples: 0 },
        { reply: 1, samples: 1200 },
      ])
      stop()
      clock.currentTime = 0.1
      vi.advanceTimersByTime(500)
      expect(sent).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
