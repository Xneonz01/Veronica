// Gap-free playback of streamed 24 kHz mono PCM16 chunks via Web Audio.
// Buffers are tagged at 24 kHz; the AudioContext resamples them to its own rate.

const TTS_SAMPLE_RATE = 24000
const REPORT_EVERY_MS = 100

/** How much of a reply has played: the server's id for it, and 24 kHz samples. */
export interface ReplyPosition {
  reply: number
  samples: number
}

interface Scheduled {
  start: number
  end: number
  samples: number
}

export class PcmPlayer {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private nextTime = 0
  private sources = new Set<AudioBufferSourceNode>()
  // The reply now playing, and when each of its buffers plays, by the audio clock.
  private reply: number | null = null
  private replyAudio: Scheduled[] = []
  /** Told when her voice starts playing and when the last scheduled audio has
   *  finished, or was cut off. Only on those changes, never per chunk. */
  onPlayingChange: ((playing: boolean) => void) | null = null

  private context(): AudioContext {
    if (this.ctx === null) {
      this.ctx = new AudioContext()
      // Sources route through an analyser (analyser -> destination) so the orb
      // can follow the voice's amplitude while it speaks.
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.connect(this.ctx.destination)
    }
    return this.ctx
  }

  // The output tap the orb reads (time-domain RMS) while Veronica speaks.
  getAnalyser(): AnalyserNode | null {
    return this.analyser
  }

  /** Whether any of her audio is scheduled or sounding right now. */
  get playing(): boolean {
    return this.sources.size > 0
  }

  // Route playback to a chosen speaker (AudioContext.setSinkId, Chromium >= 110).
  // Degrades silently to the default device where unsupported.
  async setSinkId(sinkId: string): Promise<void> {
    const ctx = this.context() as AudioContext & { setSinkId?: (id: string) => Promise<void> }
    if (typeof ctx.setSinkId !== 'function') return
    try {
      await ctx.setSinkId(sinkId)
    } catch {
      // invalid or unavailable sink: keep the current output
    }
  }

  async resume(): Promise<void> {
    await this.context().resume()
  }

  /** Audio from here on is reply `id`'s. The server marks each reply's start in the
   *  stream, ahead of its first audio, so the two can't arrive out of order. */
  beginReply(id: number): void {
    this.reply = id
    this.replyAudio = []
  }

  /** How much of the current reply has played so far, by the audio clock; null when no
   *  reply is playing. The server keeps only these words in the conversation when the
   *  user talks over her. */
  replyPosition(): ReplyPosition | null {
    if (this.reply === null || this.ctx === null) return null
    const now = this.ctx.currentTime
    let played = 0
    for (const item of this.replyAudio) {
      if (now >= item.end) played += item.samples
      else if (now > item.start) played += Math.round(((now - item.start) / (item.end - item.start)) * item.samples)
    }
    return { reply: this.reply, samples: played }
  }

  enqueue(pcm16: ArrayBuffer): void {
    const ctx = this.context()
    const samples = new Int16Array(pcm16)
    const floats = new Float32Array(samples.length)
    for (let i = 0; i < samples.length; i++) {
      floats[i] = samples[i] / 0x8000
    }
    const buffer = ctx.createBuffer(1, floats.length, TTS_SAMPLE_RATE)
    buffer.getChannelData(0).set(floats)

    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyser ?? ctx.destination)
    if (this.sources.size === 0) this.onPlayingChange?.(true)
    this.sources.add(source)
    source.onended = () => {
      this.sources.delete(source)
      if (this.sources.size === 0) this.onPlayingChange?.(false)
    }

    const startAt = Math.max(ctx.currentTime, this.nextTime)
    source.start(startAt)
    this.nextTime = startAt + buffer.duration
    if (this.reply !== null) {
      this.replyAudio.push({ start: startAt, end: this.nextTime, samples: samples.length })
    }
  }

  // Stop and drop everything already scheduled (barge-in): pre-scheduled buffers
  // would otherwise keep playing after Veronica is interrupted.
  flush(): void {
    const wasPlaying = this.sources.size > 0
    for (const source of this.sources) {
      source.onended = null
      try {
        source.stop()
      } catch {
        // already stopped/ended
      }
    }
    this.sources.clear()
    this.nextTime = this.ctx?.currentTime ?? 0
    // Cut off: the reply is over, and its stopped buffers mustn't count as heard.
    this.reply = null
    this.replyAudio = []
    if (wasPlaying) this.onPlayingChange?.(false)
  }

  close(): void {
    this.flush()
    void this.ctx?.close()
    this.ctx = null
    this.analyser = null
    this.nextTime = 0
  }
}

/** Tell the server, every 100 ms and only when it has changed, how much of the current
 *  reply has played. It uses this to keep listening for an interruption until she has
 *  been heard to the end, and to keep only the words that were heard. Returns a stop. */
export function reportPlayback(player: PcmPlayer, send: (position: ReplyPosition) => void): () => void {
  let last: ReplyPosition | null = null
  const timer = setInterval(() => {
    const position = player.replyPosition()
    if (position === null) return
    if (last?.reply === position.reply && last.samples === position.samples) return
    last = position
    send(position)
  }, REPORT_EVERY_MS)
  return () => clearInterval(timer)
}
