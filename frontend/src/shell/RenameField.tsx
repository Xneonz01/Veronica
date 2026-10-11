// Renaming in place: the row becomes its own text field. Enter or leaving the
// field keeps the name, Escape keeps the old one. onDone gets the trimmed new name,
// or null when the edit was abandoned.

import { useEffect, useRef, useState } from 'react'
import styles from './Recents.module.css'

const MAX = 200 // the server's limit for a title

export function RenameField({
  label,
  initial,
  placeholder,
  onDone,
}: {
  label: string
  initial: string
  /** Says what to type when there is no name yet. */
  placeholder?: string
  /** `how` says what ended it: a key, or the field losing focus to a click elsewhere. */
  onDone: (name: string | null, how: 'enter' | 'escape' | 'blur') => void
}) {
  const [value, setValue] = useState(initial)
  const field = useRef<HTMLInputElement>(null)
  const finished = useRef(false)

  useEffect(() => {
    field.current?.focus()
    field.current?.select()
  }, [])

  function finish(name: string | null, how: 'enter' | 'escape' | 'blur') {
    if (finished.current) return // Enter then the blur it causes: act once
    finished.current = true
    onDone(name === null ? null : name.trim() || null, how)
  }

  return (
    <input
      ref={field}
      className={styles.rename}
      aria-label={label}
      placeholder={placeholder}
      value={value}
      maxLength={MAX}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          // Handled here: the owner may move focus to a button as this ends, and the
          // key's own keypress would then press that button too.
          event.preventDefault()
          finish(value, 'enter')
        }
        if (event.key === 'Escape') {
          event.stopPropagation()
          finish(null, 'escape')
        }
      }}
      onBlur={() => finish(value, 'blur')}
    />
  )
}
