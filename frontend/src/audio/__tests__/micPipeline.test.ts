// The call's microphone: switches that overlap, mute that follows the device, and a
// device that arrives after the call has closed.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MicCapture } from '../micCapture'
import { AudioUnavailableError, MicPipeline } from '../micPipeline'

const capture = vi.hoisted(() => ({ acquire: vi.fn() }))
vi.mock('../micCapture', () => ({ acquireMic: capture.acquire }))

class FakeTrack {
  enabled = true
  stopped = false
  stop() {
    this.stopped = true
  }
}

function fakeCapture(): MicCapture & { track: FakeTrack } {
  const track = new FakeTrack()
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream
  const source = { disconnect: vi.fn() } as unknown as MediaStreamAudioSourceNode
  return { stream, source, analyser: {} as AnalyserNode, track }
}

let addModule = vi.fn(async () => undefined)

beforeEach(() => {
  addModule = vi.fn(async () => undefined)
  vi.stubGlobal(
    'AudioContext',
    class {
      audioWorklet = { addModule }
      close = vi.fn(async () => undefined)
    },
  )
  vi.stubGlobal(
    'AudioWorkletNode',
    class {
      port = { onmessage: null }
      disconnect = vi.fn()
    },
  )
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

async function opened() {
  const first = fakeCapture()
  capture.acquire.mockResolvedValueOnce(first)
  const mic = await MicPipeline.open('', () => undefined, () => undefined)
  return { mic, first }
}

describe('the call microphone', () => {
  it('keeps the newest of two overlapping switches', async () => {
    const { mic, first } = await opened()
    const slow = fakeCapture()
    const fast = fakeCapture()
    let arriveSlow: (value: MicCapture) => void = () => undefined
    capture.acquire.mockReturnValueOnce(new Promise((resolve) => (arriveSlow = resolve)))
    capture.acquire.mockResolvedValueOnce(fast)

    const older = mic.switchTo('usb')
    const newer = mic.switchTo('desk')
    expect(await newer).toBe('switched')
    arriveSlow(slow)
    expect(await older).toBe('superseded')
    expect(slow.track.stopped).toBe(true) // the late device is let go
    expect(first.track.stopped).toBe(true)
    expect(fast.track.stopped).toBe(false)
  })

  it('carries mute over to the new device', async () => {
    const { mic } = await opened()
    mic.setMuted(true)
    const next = fakeCapture()
    capture.acquire.mockResolvedValueOnce(next)
    expect(await mic.switchTo('desk')).toBe('switched')
    expect(next.track.enabled).toBe(false)
  })

  it('keeps the working device when the new one fails', async () => {
    const { mic, first } = await opened()
    capture.acquire.mockRejectedValueOnce(new DOMException('busy', 'NotReadableError'))
    expect(await mic.switchTo('busy-mic')).toBe('failed')
    expect(first.track.stopped).toBe(false)
  })

  it('lets go of a device that arrives after closing', async () => {
    const { mic } = await opened()
    const late = fakeCapture()
    let arrive: (value: MicCapture) => void = () => undefined
    capture.acquire.mockReturnValueOnce(new Promise((resolve) => (arrive = resolve)))
    const pending = mic.switchTo('usb')
    mic.close()
    arrive(late)
    expect(await pending).toBe('superseded')
    expect(late.track.stopped).toBe(true)
  })

  it('tells a worklet that will not load apart from a refused microphone', async () => {
    addModule.mockRejectedValueOnce(new DOMException('load failed', 'AbortError'))
    await expect(MicPipeline.open('', () => undefined, () => undefined)).rejects.toBeInstanceOf(AudioUnavailableError)
    capture.acquire.mockRejectedValueOnce(new DOMException('no', 'NotAllowedError'))
    await expect(MicPipeline.open('', () => undefined, () => undefined)).rejects.toMatchObject({ name: 'NotAllowedError' })
  })
})
