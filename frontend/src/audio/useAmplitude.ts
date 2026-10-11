// Amplitude taps for her orb. Plain helpers, not a React hook: the orb reads
// them inside its requestAnimationFrame loop rather than through React state, since
// a setState per frame would re-render the tree on every frame.

// Tap a source (e.g. the mic) with an AnalyserNode for RMS reads. The analyser is
// not connected on to the destination; it only observes, so the mic is never echoed.
export function createTapAnalyser(ctx: AudioContext, source: AudioNode): AnalyserNode {
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.8
  source.connect(analyser)
  return analyser
}

// Time-domain RMS in roughly [0, 1]; 0 when the analyser is absent or silent.
// `buf` is a reusable scratch array (length >= analyser.fftSize) owned by the
// caller so the hot loop allocates nothing.
export function readRms(analyser: AnalyserNode | null, buf: Float32Array<ArrayBuffer>): number {
  if (analyser === null) return 0
  analyser.getFloatTimeDomainData(buf)
  let sum = 0
  for (let i = 0; i < buf.length; i++) {
    sum += buf[i] * buf[i]
  }
  return Math.sqrt(sum / buf.length)
}
