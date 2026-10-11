// A switch for a setting that applies the moment it flips (role="switch"). Its
// label names the setting and the description says what it changes.

import { useId, type ReactNode } from 'react'
import styles from './Field.module.css'

export type SwitchProps = {
  label: string
  description?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}

export function Switch({ label, description, checked, onChange, disabled = false }: SwitchProps) {
  const id = useId()
  return (
    <div className={styles.switchRow}>
      <span className={styles.switchText}>
        <span id={`${id}-label`} className={styles.switchLabel}>
          {label}
        </span>
        {description != null && (
          <span id={`${id}-description`} className={styles.hint}>
            {description}
          </span>
        )}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description != null ? `${id}-description` : undefined}
        disabled={disabled}
        className={styles.switch}
        onClick={() => onChange(!checked)}
      >
        <span className={styles.thumb} aria-hidden="true" />
      </button>
    </div>
  )
}
