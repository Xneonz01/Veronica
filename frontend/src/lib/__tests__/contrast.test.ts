import { describe, expect, it } from 'vitest'
import { composite, contrastRatio, oklchToSrgb, parseColor } from '../contrast'

function hex(css: string): string {
  const colour = parseColor(css)
  if (!colour) throw new Error(`unreadable: ${css}`)
  return [colour.r, colour.g, colour.b].map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('')
}

function ratio(fg: string, bg: string): number {
  const [a, b] = [parseColor(fg), parseColor(bg)]
  if (!a || !b) throw new Error('unreadable colour')
  return contrastRatio(a, b)
}

describe('contrast', () => {
  it('converts OKLCH to the sRGB values browsers render', () => {
    expect(hex('oklch(0.696 0.149 162.5)')).toBe('10b981') // Tailwind's emerald-500
    const [r, g, b] = oklchToSrgb(1, 0, 0)
    expect([r, g, b].map((v) => Math.round(v * 255))).toEqual([255, 255, 255])
  })

  it('reproduces published ratios', () => {
    // #047857 on white is 5.48:1 in every WCAG checker.
    expect(ratio('rgb(255, 255, 255)', 'rgb(4, 120, 87)')).toBeCloseTo(5.48, 2)
    expect(ratio('rgb(0, 0, 0)', 'rgb(255, 255, 255)')).toBeCloseTo(21, 5)
  })

  it('reads the forms a computed style comes back in', () => {
    expect(parseColor('oklch(0.51 0.11 165 / 0.1)')?.alpha).toBeCloseTo(0.1)
    expect(parseColor('rgba(10, 20, 30, 0.5)')).toEqual({ r: 10 / 255, g: 20 / 255, b: 30 / 255, alpha: 0.5 })
    expect(parseColor('color(srgb 1 0 0 / 0.25)')).toEqual({ r: 1, g: 0, b: 0, alpha: 0.25 })
    expect(parseColor('oklch(51% 0.11 165)')?.alpha).toBe(1)
    expect(parseColor('oklab(0.5 0.1 0.1)')).toBeNull() // not a form our tokens produce
  })

  it('lays a translucent tint over its ground before measuring', () => {
    const tint = parseColor('rgba(0, 0, 0, 0.5)')!
    const white = parseColor('rgb(255, 255, 255)')!
    const grey = composite(tint, white)
    expect(grey.r).toBeCloseTo(0.5)
    expect(grey.alpha).toBe(1)
  })
})
