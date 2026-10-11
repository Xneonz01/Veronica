// A shortcut you set by pressing it, so nobody has to guess whether it is written
// "Ctrl+Alt+K" or "Control+Alt+K". While it listens every key goes to it: a
// half-pressed chord must not close a window or move the focus. Escape, a click
// or leaving it stops listening and keeps the shortcut as it was.

import { useEffect, useState, type ReactNode } from 'react'
import { Notice } from '../ui/Feedback'
import { Kbd } from '../ui/Kbd'
import { cx } from '../ui/cx'
import { Row } from './Group'
import { readChord, REFUSED_COPY, shownKey, type Chord } from './hotkeys'
import styles from './HotkeyField.module.css'


export function HotkeyRow({
  label,
  description,
  value,
  problem,
  onChange,
}: {
  label: string
  description: ReactNode
  value: string
  /** Why the shortcut isn't working as asked, from the desktop shell. */
  problem: string | null
  onChange: (hotkey: string) => void
}) {
  const [listening, setListening] = useState(false)
  const [chord, setChord] = useState<Chord | null>(null)

  useEffect(() => {
    if (!listening) return
    const stop = () => {
      setListening(false)
      setChord(null)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault()
      event.stopPropagation()
      const plain = !event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey
      if (event.key === 'Escape' && plain) return stop()
      const next = readChord(event)
      if (next.state !== 'done') return setChord(next)
      stop()
      if (next.accelerator !== value) onChange(next.accelerator)
    }
    // Letting go of a modifier takes it off the keys shown so far.
    const onKeyUp = (event: KeyboardEvent) => {
      event.stopPropagation()
      setChord((current) => (current?.state === 'held' ? readChord(event) : current))
    }
    window.addEventListener('keydown', onKeyDown, true)
    window.addEventListener('keyup', onKeyUp, true)
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      window.removeEventListener('keyup', onKeyUp, true)
    }
  }, [listening, onChange, value])

  const keys = listening ? (chord?.state === 'held' ? chord.keys : []) : value.split('+')
  const hint = chord?.state === 'refused' ? REFUSED_COPY[chord.reason] : 'Press the new combination. Esc cancels.'

  return (
    <Row
      label={label}
      description={description}
      below={
        <>
          <p className={styles.hint} aria-live="polite">
            {listening ? hint : ''}
          </p>
          {!listening && problem && (
            <Notice tone="attention" live>
              {problem}
            </Notice>
          )}
        </>
      }
    >
      <button
        type="button"
        className={cx(styles.field, listening && styles.listening)}
        aria-label={listening ? `${label}: listening for the new combination` : `${label}: ${value.split('+').map(shownKey).join(' ')}. Change it`}
        onClick={() => {
          setListening((on) => !on)
          setChord(null)
        }}
        onBlur={() => {
          setListening(false)
          setChord(null)
        }}
      >
        {keys.map((key, index) => (
          <Kbd key={`${index}-${key}`} className={styles.key}>
            {shownKey(key)}
          </Kbd>
        ))}
        {listening && <span className={styles.waiting}>{keys.length > 0 ? '…' : 'Press keys'}</span>}
      </button>
    </Row>
  )
}
