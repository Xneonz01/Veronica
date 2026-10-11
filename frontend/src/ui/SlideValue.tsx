// Figures change by replacement, never by counting up: a count-up invents the
// numbers in between, and a figure the system produced is shown exactly as it
// came. The key on the inner span replays the slide each time the value changes.

import { cx } from './cx'
import styles from './SlideValue.module.css'

export function SlideValue({ value, className }: { value: string; className?: string }) {
  return (
    <span className={cx(styles.window, 'num', className)}>
      <span key={value} className={styles.value}>
        {value}
      </span>
    </span>
  )
}
