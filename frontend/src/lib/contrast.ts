// WCAG contrast for the colours the browser computes, so the design page can
// measure every text pair from the live tokens instead of quoting numbers that
// drift. Colours are gamma-encoded sRGB in 0..1; alpha is composited in that
// space, as browsers composite.

export type Rgba = { r: number; g: number; b: number; alpha: number }

const NUMBER = String.raw`(-?[\d.]+%?)`

// Chromium serialises a computed colour in the space it was written in: these
// are the forms our tokens produce.
const OKLCH = new RegExp(String.raw`^oklch\(${NUMBER}\s+${NUMBER}\s+${NUMBER}(?:\s*/\s*${NUMBER})?\)$`)
const RGB = new RegExp(String.raw`^rgba?\(${NUMBER}[,\s]+${NUMBER}[,\s]+${NUMBER}(?:\s*[,/]\s*${NUMBER})?\)$`)
const SRGB = new RegExp(String.raw`^color\(srgb\s+${NUMBER}\s+${NUMBER}\s+${NUMBER}(?:\s*/\s*${NUMBER})?\)$`)

function part(value: string, scale = 1): number {
  return value.endsWith('%') ? parseFloat(value) / 100 : parseFloat(value) / scale
}

export function oklchToSrgb(l: number, c: number, h: number): [number, number, number] {
  const a = c * Math.cos((h * Math.PI) / 180)
  const b = c * Math.sin((h * Math.PI) / 180)
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3
  const linear = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ]
  const encode = (x: number) => {
    const v = Math.min(1, Math.max(0, x))
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
  }
  return [encode(linear[0]), encode(linear[1]), encode(linear[2])]
}

/** A computed CSS colour as sRGB, or null for a form we don't read. */
export function parseColor(css: string): Rgba | null {
  const text = css.trim()
  let match = OKLCH.exec(text)
  if (match) {
    const [r, g, b] = oklchToSrgb(part(match[1]), part(match[2]), parseFloat(match[3]))
    return { r, g, b, alpha: match[4] ? part(match[4]) : 1 }
  }
  match = RGB.exec(text)
  if (match) {
    return {
      r: part(match[1], 255),
      g: part(match[2], 255),
      b: part(match[3], 255),
      alpha: match[4] ? part(match[4]) : 1,
    }
  }
  match = SRGB.exec(text)
  if (match) {
    return { r: part(match[1]), g: part(match[2]), b: part(match[3]), alpha: match[4] ? part(match[4]) : 1 }
  }
  return null
}

/** `top` laid over an opaque `under`. */
export function composite(top: Rgba, under: Rgba): Rgba {
  const mix = (a: number, b: number) => a * top.alpha + b * (1 - top.alpha)
  return { r: mix(top.r, under.r), g: mix(top.g, under.g), b: mix(top.b, under.b), alpha: 1 }
}

function luminance({ r, g, b }: Rgba): number {
  const linear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
}

/** WCAG 2 contrast ratio between two opaque colours. */
export function contrastRatio(a: Rgba, b: Rgba): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
