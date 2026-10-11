// A segmented control: one choice among a few, all visible. It is a radio group
// with a roving tab stop, so Tab enters and leaves it in one step and the arrow
// keys, Home and End move the selection (and the focus with it).

import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from './cx'
import styles from './Segmented.module.css'

export type SegmentOption<T extends string> = {
  value: T
  label: ReactNode
  icon?: ReactNode
  /** The accessible name when `label` is only an icon or is abbreviated. */
  title?: string
}

export type SegmentedProps<T extends string> = {
  label: string
  value: T
  options: readonly SegmentOption<T>[]
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  className?: string
}

export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  size = 'md',
  className,
}: SegmentedProps<T>) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const selected = options.findIndex((option) => option.value === value)

  function choose(index: number) {
    const wrapped = (index + options.length) % options.length
    onChange(options[wrapped].value)
    buttons.current[wrapped]?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = Math.max(selected, 0)
    const moves: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: options.length - 1,
    }
    if (!(event.key in moves)) return
    event.preventDefault()
    choose(moves[event.key])
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(styles.group, size === 'sm' && styles.sm, className)}
      onKeyDown={onKeyDown}
    >
      {options.map((option, index) => {
        const checked = option.value === value
        return (
          <button
            key={option.value}
            ref={(element) => {
              buttons.current[index] = element
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={option.title}
            title={option.title}
            // One tab stop: the checked option, or the first when none is.
            tabIndex={checked || (selected === -1 && index === 0) ? 0 : -1}
            className={styles.option}
            onClick={() => onChange(option.value)}
          >
            {option.icon}
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
