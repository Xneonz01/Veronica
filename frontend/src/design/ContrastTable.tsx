// Every text pair, measured from the tokens as this island computes them. A
// translucent tint is laid over the sheet it sits on, and a translucent ink over
// its own ground, before the ratio is taken, so the numbers are what you see.

import { useLayoutEffect, useRef, useState } from 'react'
import { composite, contrastRatio, parseColor } from '../lib/contrast'
import styles from './Foundations.module.css'

type Pair = { label: string; fg: string; bg: string; min: number }

const PAIRS: Pair[] = [
  { label: 'Ink on the page', fg: 'foreground', bg: 'background', min: 4.5 },
  { label: 'Ink on the sheet', fg: 'foreground', bg: 'surface', min: 4.5 },
  { label: 'Secondary text', fg: 'muted-foreground', bg: 'surface', min: 4.5 },
  { label: 'Tertiary text', fg: 'subtle-foreground', bg: 'surface', min: 4.5 },
  { label: 'Tertiary on a quiet fill', fg: 'subtle-foreground', bg: 'muted', min: 4.5 },
  { label: 'Her emerald as text', fg: 'assistant', bg: 'surface', min: 4.5 },
  { label: 'Primary label', fg: 'primary-foreground', bg: 'primary', min: 4.5 },
  { label: 'Focus ring (not text)', fg: 'ring', bg: 'background', min: 3 },
  { label: 'Information', fg: 'info', bg: 'surface', min: 4.5 },
  { label: 'Attention', fg: 'warning', bg: 'surface', min: 4.5 },
  { label: 'Refusal', fg: 'destructive', bg: 'surface', min: 4.5 },
  { label: 'Done', fg: 'success', bg: 'surface', min: 4.5 },
  { label: 'Veronica tint', fg: 'assistant-on-soft', bg: 'assistant-soft', min: 4.5 },
  { label: 'Information tint', fg: 'info-on-soft', bg: 'info-soft', min: 4.5 },
  { label: 'Attention tint', fg: 'warning-on-soft', bg: 'warning-soft', min: 4.5 },
  { label: 'Refusal tint', fg: 'destructive-on-soft', bg: 'destructive-soft', min: 4.5 },
  { label: 'Decoration, never text', fg: 'faint-foreground', bg: 'surface', min: 0 },
]

export function ContrastTable() {
  const table = useRef<HTMLDivElement>(null)
  const [ratios, setRatios] = useState<(number | null)[]>([])

  useLayoutEffect(() => {
    const root = table.current
    if (!root) return
    const sheet = parseColor(getComputedStyle(root).backgroundColor)
    const samples = root.querySelectorAll<HTMLElement>('[data-sample]')
    setRatios(
      Array.from(samples, (sample) => {
        const style = getComputedStyle(sample)
        let fg = parseColor(style.color)
        let bg = parseColor(style.backgroundColor)
        if (!fg || !bg || !sheet) return null
        if (bg.alpha < 1) bg = composite(bg, sheet)
        if (fg.alpha < 1) fg = composite(fg, bg)
        return contrastRatio(fg, bg)
      }),
    )
  }, [])

  return (
    <div ref={table} className={styles.contrast} role="table" aria-label="Measured contrast">
      {PAIRS.map((pair, index) => {
        const ratio = ratios[index]
        const passes = ratio !== undefined && ratio !== null && ratio >= pair.min
        return (
          <div key={pair.label} className={styles.contrastRow} role="row">
            <span
              data-sample
              className={styles.sample}
              style={{ color: `var(--${pair.fg})`, background: `var(--${pair.bg})` }}
              role="cell"
              aria-hidden="true"
            >
              Aa
            </span>
            <span className={styles.contrastLabel} role="cell">
              {pair.label}
              <code className={styles.token}>
                {pair.fg} on {pair.bg}
              </code>
            </span>
            <span className={`${styles.ratio} num`} role="cell">
              {ratio == null ? '—' : `${ratio.toFixed(2)}:1`}
            </span>
            <span className={styles.verdict} data-pass={pair.min === 0 || passes} role="cell">
              {pair.min === 0 ? 'decoration' : passes ? `passes ${pair.min}` : `below ${pair.min}`}
            </span>
          </div>
        )
      })}
    </div>
  )
}
