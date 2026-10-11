// The microphone side of a call: an audio context feeding the capture worklet from
// the chosen input, live device switching, and mute. The call hook owns the socket
// and the call's state; this owns the audio graph.

import { acquireMic, type MicCapture } from './micCapture'

/** The capture worklet couldn't load: the page can't record at all. Kept apart from
    a microphone the browser refused or couldn't open, which throws a DOMException. */
export class AudioUnavailableError extends Error {}

/** `superseded`: a later switch, or closing, overtook this one while it waited. */
export type SwitchResult = 'switched' | 'failed' | 'superseded'

export class MicPipeline {
  private readonly context: AudioContext
  private readonly worklet: AudioWorkletNode
  private readonly onEnded: () => void
  private capture: MicCapture
  private switches = 0
  private muted = false
  private closed = false

  private constructor(context: AudioContext, worklet: AudioWorkletNode, capture: MicCapture, onEnded: () => void) {
    this.context = context
    this.worklet = worklet
    this.capture = capture
    this.onEnded = onEnded
  }

  /** Open `deviceId` (the system default for '') and pass each captured frame to
      `send`. `onEnded` hears the device go away. On failure everything opened so
      far is released before the error is thrown. */
  static async open(deviceId: string, send: (frame: ArrayBuffer) => void, onEnded: () => void): Promise<MicPipeline> {
    const context = new AudioContext()
    let worklet: AudioWorkletNode
    try {
      await context.audioWorklet.addModule('/audio/capture-processor.js')
      worklet = new AudioWorkletNode(context, 'capture-processor')
    } catch {
      context.close().catch(() => undefined)
      throw new AudioUnavailableError('the capture worklet did not load')
    }
    worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => send(event.data)
    try {
      const capture = await acquireMic(context, deviceId, worklet, onEnded)
      return new MicPipeline(context, worklet, capture, onEnded)
    } catch (err) {
      worklet.port.onmessage = null
      context.close().catch(() => undefined)
      throw err
    }
  }

  get analyser(): AnalyserNode {
    return this.capture.analyser
  }

  /** Move to another input. The new microphone opens before the old one is let go,
      so a busy device leaves the working one connected. */
  async switchTo(deviceId: string): Promise<SwitchResult> {
    const mine = ++this.switches
    let next: MicCapture
    try {
      next = await acquireMic(this.context, deviceId, this.worklet, this.onEnded)
    } catch {
      return this.closed || mine !== this.switches ? 'superseded' : 'failed'
    }
    if (this.closed || mine !== this.switches) {
      release(next)
      return 'superseded'
    }
    release(this.capture)
    this.capture = next
    this.applyMute()
    return 'switched'
  }

  setMuted(muted: boolean): void {
    this.muted = muted
    this.applyMute()
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    release(this.capture)
    this.worklet.port.onmessage = null
    this.worklet.disconnect()
    this.context.close().catch(() => undefined)
  }

  private applyMute(): void {
    this.capture.stream.getAudioTracks().forEach((track) => (track.enabled = !this.muted))
  }
}

// Stopping a track doesn't fire its `ended` event, so letting go of a microphone
// here is never mistaken for it being unplugged.
function release(capture: MicCapture): void {
  capture.source.disconnect()
  capture.stream.getTracks().forEach((track) => track.stop())
}
