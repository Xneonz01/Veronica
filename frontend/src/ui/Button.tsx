// Buttons: one shape, five voices (Button.module.css explains each). The primary
// voice is emerald because it is Veronica acting; everything else stays neutral
// so the emerald means something.

import type { ComponentPropsWithRef, ReactNode } from 'react'
import styles from './Button.module.css'
import { cx } from './cx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'quiet' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: ReactNode
  trailingIcon?: ReactNode
  wide?: boolean
  /** The spinner replaces the icon and the button stops taking clicks. */
  loading?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  trailingIcon,
  wide = false,
  loading = false,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={cx(styles.button, styles[variant], styles[size], wide && styles.wide, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? <Spinner /> : icon}
      {children != null && <span className={styles.label}>{children}</span>}
      {!loading && trailingIcon}
    </button>
  )
}

export type IconButtonProps = Omit<ComponentPropsWithRef<'button'>, 'children'> & {
  /** The accessible name, and the hint on hover. Required: an icon is not a name. */
  label: string
  icon: ReactNode
  variant?: ButtonVariant
  size?: ButtonSize
  pressed?: boolean
  /** Circular rather than rounded-square: for floating controls. */
  round?: boolean
}

export function IconButton({
  label,
  icon,
  variant = 'ghost',
  size = 'md',
  pressed,
  round = false,
  className,
  type = 'button',
  ...rest
}: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={cx(
        styles.button,
        styles[variant],
        styles[size],
        styles.iconOnly,
        round && styles.round,
        className,
      )}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
    >
      {icon}
    </button>
  )
}

function Spinner() {
  return <span className={styles.spinner} aria-hidden="true" />
}
