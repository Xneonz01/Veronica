// Acquire the mic for the live capture path: getUserMedia with the chosen input
// device, tap it with an analyser, and feed the capture worklet. Used at call
// start and for live mid-call device switching (re-acquire without a call drop).

import { createTapAnalyser } from './useAmplitude'

export interface MicCapture {
  stream: MediaStream
  source: MediaStreamAudioSourceNode
  analyser: AnalyserNode
}

const micConstraints = (deviceId: string): MediaStreamConstraints => ({
  audio: {
    deviceId: deviceId ? { exact: deviceId } : undefined,
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
})

function errorName(err: unknown): string {
  return typeof err === 'object' && err !== null && 'name' in err
    ? String((err as { name: unknown }).name)
    : ''
}

// Open the chosen mic. If that exact device is gone (saved earlier, since
// unplugged), fall back to the system default instead of failing the whole call.
// A real permission denial (NotAllowedError/SecurityError) still propagates.
async function openMicStream(deviceId: string): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia(micConstraints(deviceId))
  } catch (err) {
    const name = errorName(err)
    if (deviceId && (name === 'OverconstrainedError' || name === 'NotFoundError')) {
      return navigator.mediaDevices.getUserMedia(micConstraints(''))
    }
    throw err
  }
}

export async function acquireMic(
  ctx: AudioContext,
  deviceId: string,
  worklet: AudioWorkletNode,
  onEnded: () => void,
): Promise<MicCapture> {
  const stream = await openMicStream(deviceId)
  try {
    stream.getAudioTracks().forEach((track) => {
      track.onended = onEnded // fires when the chosen device is unplugged
    })
    const source = ctx.createMediaStreamSource(stream)
    const analyser = createTapAnalyser(ctx, source)
    source.connect(worklet) // not to destination: don't echo the mic
    return { stream, source, analyser }
  } catch (err) {
    // The context can close while the permission prompt is open (the page was
    // left); the microphone must not stay on behind it.
    stream.getTracks().forEach((track) => track.stop())
    throw err
  }
}
