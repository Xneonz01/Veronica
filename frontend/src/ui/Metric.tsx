// One figure with its label, and optionally its unit, change and caption. The
// value is shown exactly as the system produced it; this component never formats
// or recomputes it.

import type { ReactNode } from 'react'
import { cx } from './cx'
import { deltaDirection } from './delta'
import styles from './Metric.module.css'
import { SlideValue } from './SlideValue'
import { Surface, type Material } from './Surface'

export type MetricProps = {
  label: ReactNode
  /** The figure exactly as the system printed it. */
  value: string
  unit?: ReactNode
  caption?: ReactNode
  /** A leading + reads as up, a minus (− or -) as down, anything else as flat. */
  delta?: string
  tone?: 'neutral' | 'success' | 'info' | 'assistant'
  material?: Material
  size?: 'md' | 'lg'
  footer?: ReactNode
  className?: string
}

export function Metric({
  label,
  value,
  unit,
  caption,
  delta,
  tone = 'neutral',
  material = 'flat',
  size = 'md',
  footer,
  className,
}: MetricProps) {
  const direction = delta === undefined ? null : deltaDirection(delta)
  return (
    <Surface material={material} radius="xl" className={cx(styles.metric, size === 'lg' && styles.lg, className)}>
      <span className={styles.label}>{label}</span>
      <span className={styles.line}>
        <SlideValue value={value} className={cx(styles.value, tone !== 'neutral' && styles[tone])} />
        {unit != null && <span className={styles.unit}>{unit}</span>}
        {delta !== undefined && direction !== null && (
          <span className={cx(styles.delta, styles[direction])} data-direction={direction}>
            {delta}
          </span>
        )}
      </span>
      {caption != null && <span className={styles.caption}>{caption}</span>}
      {footer != null && <div className={styles.footer}>{footer}</div>}
    </Surface>
  )
}
