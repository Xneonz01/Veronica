// AudioWorklet: downsample mic audio to 16 kHz mono PCM16 and post 20 ms frames.
// Loaded by URL (must be a standalone classic worklet module, not bundled).

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.targetRate = 16000
    this.ratio = sampleRate / this.targetRate // input (ctx) rate -> 16 kHz
    this.frameSamples = 320 // 20 ms at 16 kHz
    this.resampled = []
    this.readPos = 0 // fractional read offset carried across blocks
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const channel = input[0]

    let i = this.readPos
    for (; i < channel.length; i += this.ratio) {
      const idx = Math.floor(i)
      const frac = i - idx
      const next = idx + 1 < channel.length ? channel[idx + 1] : channel[idx]
      this.resampled.push(channel[idx] * (1 - frac) + next * frac)
    }
    this.readPos = i - channel.length

    while (this.resampled.length >= this.frameSamples) {
      const frame = this.resampled.splice(0, this.frameSamples)
      const pcm = new Int16Array(this.frameSamples)
      for (let j = 0; j < this.frameSamples; j++) {
        const s = Math.max(-1, Math.min(1, frame[j]))
        pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer])
    }
    return true
  }
}

registerProcessor('capture-processor', CaptureProcessor)
