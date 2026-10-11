// Text fields: a label above, one hairline, an emerald focus, and an error that
// is a sentence linked to the control (aria-describedby), never a red border
// alone. The label is always there for a screen reader, even when hidden.

import { ChevronDown, CircleAlert, Search } from 'lucide-react'
import { useId, type ComponentPropsWithRef, type ReactNode } from 'react'
import { cx } from './cx'
import styles from './Field.module.css'

type FieldShell = {
  label: string
  hint?: ReactNode
  error?: ReactNode
  /** Keep the label for assistive technology but not on screen. */
  hideLabel?: boolean
}

type Described = {
  id: string
  describedBy: string | undefined
  invalid: boolean
}

function useDescribed(id: string | undefined, hint: ReactNode, error: ReactNode): Described {
  const generated = useId()
  const base = id ?? generated
  const parts = [hint != null && `${base}-hint`, error != null && `${base}-error`].filter(Boolean)
  return { id: base, describedBy: parts.length ? parts.join(' ') : undefined, invalid: error != null }
}

function Shell({
  field,
  label,
  hint,
  error,
  hideLabel,
  children,
}: FieldShell & { field: Described; children: ReactNode }) {
  return (
    <div className={styles.field}>
      <label htmlFor={field.id} className={hideLabel ? 'visually-hidden' : styles.label}>
        {label}
      </label>
      {children}
      {hint != null && (
        <p id={`${field.id}-hint`} className={styles.hint}>
          {hint}
        </p>
      )}
      {error != null && (
        <p id={`${field.id}-error`} className={styles.error} role="alert">
          <CircleAlert size={14} aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  )
}

export type TextFieldProps = FieldShell & ComponentPropsWithRef<'input'>

export function TextField({ label, hint, error, hideLabel, id, className, ...rest }: TextFieldProps) {
  const field = useDescribed(id, hint, error)
  return (
    <Shell field={field} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      <input
        {...rest}
        id={field.id}
        className={cx(styles.control, field.invalid && styles.invalid, className)}
        aria-invalid={field.invalid || undefined}
        aria-describedby={field.describedBy}
      />
    </Shell>
  )
}

export type TextAreaProps = FieldShell & ComponentPropsWithRef<'textarea'>

export function TextArea({ label, hint, error, hideLabel, id, className, ...rest }: TextAreaProps) {
  const field = useDescribed(id, hint, error)
  return (
    <Shell field={field} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      <textarea
        {...rest}
        id={field.id}
        className={cx(styles.control, styles.area, field.invalid && styles.invalid, className)}
        aria-invalid={field.invalid || undefined}
        aria-describedby={field.describedBy}
      />
    </Shell>
  )
}

export type SelectOption = { value: string; label: string }

export type SelectFieldProps = FieldShell &
  Omit<ComponentPropsWithRef<'select'>, 'children'> & { options: readonly SelectOption[] }

/** The native select, restyled: the platform's own list is the accessible one. */
export function SelectField({
  label,
  hint,
  error,
  hideLabel,
  id,
  className,
  options,
  ...rest
}: SelectFieldProps) {
  const field = useDescribed(id, hint, error)
  return (
    <Shell field={field} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      <div className={styles.selectWrap}>
        <select
          {...rest}
          id={field.id}
          className={cx(styles.control, styles.select, field.invalid && styles.invalid, className)}
          aria-invalid={field.invalid || undefined}
          aria-describedby={field.describedBy}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown size={16} className={styles.chevron} aria-hidden="true" />
      </div>
    </Shell>
  )
}

export type SearchFieldProps = Omit<FieldShell, 'hideLabel'> & ComponentPropsWithRef<'input'>

/** A search box's purpose is its icon, so its label is for screen readers only. */
export function SearchField({ label, hint, error, id, className, ...rest }: SearchFieldProps) {
  const field = useDescribed(id, hint, error)
  return (
    <Shell field={field} label={label} hint={hint} error={error} hideLabel>
      <div className={styles.search}>
        <Search size={17} className={styles.searchIcon} aria-hidden="true" />
        <input
          {...rest}
          id={field.id}
          type="search"
          className={cx(styles.control, styles.searchInput, field.invalid && styles.invalid, className)}
          aria-invalid={field.invalid || undefined}
          aria-describedby={field.describedBy}
        />
      </div>
    </Shell>
  )
}
