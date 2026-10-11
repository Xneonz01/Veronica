// A checkbox for agreeing to something or choosing it among others: a native
// input, so the keyboard and screen readers get the real thing, drawn in the
// system's shape. A setting that applies at once is a Switch instead.

import { useId, type ReactNode } from 'react'
import styles from './Checkbox.module.css'

export type CheckboxProps = {
  label: ReactNode
  description?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}

export function Checkbox({ label, description, checked, onChange, disabled = false }: CheckboxProps) {
  const id = useId()
  return (
    <label className={styles.row} htmlFor={id}>
      <input
        id={id}
        type="checkbox"
        className={styles.box}
        checked={checked}
        disabled={disabled}
        aria-describedby={description != null ? `${id}-description` : undefined}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={styles.text}>
        <span className={styles.label}>{label}</span>
        {description != null && (
          <span id={`${id}-description`} className={styles.description}>
            {description}
          </span>
        )}
      </span>
    </label>
  )
}
