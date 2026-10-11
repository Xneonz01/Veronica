// Her requests for permission, over whatever page is open. A request she can't
// act on without you is never a toast: it stays until you answer or it runs out,
// and running out means no.
//
// It takes the keyboard only when nothing is being typed. An Enter meant for the
// composer must never land on Allow.

import { ShieldQuestionMark } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { useConfirmations, type Confirmation } from '../kernel/useConfirmations'
import { capabilityRequest } from '../lib/capabilities'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Countdown } from '../ui/Countdown'
import styles from './PermissionPrompts.module.css'
import { useToasts } from './toast'

// Said when she asks because of what she read: where the words came from, which is all
// the check knows, and not who wanted it done.
const FROM_READING = 'The words for this came from something I read, not from you.'
const AFTER_WEB = "I read something on the web just before this, so I'm checking with you."

export function PermissionPrompts() {
  const { pending, answer } = useConfirmations()
  const toasts = useToasts()
  // A prompt that takes the keyboard is read out as the dialog it is. One that
  // arrives while you type can't take it, so it is announced here instead: never
  // both, or it is read twice.
  const [announcement, setAnnouncement] = useState('')
  const announce = useCallback((text: string) => setAnnouncement(`Veronica is asking: ${text}`), [])

  async function reply(token: string, approved: boolean) {
    if ((await answer(token, approved)) || !approved) return
    toasts.show({ tone: 'critical', title: "Your OK didn't reach her", body: 'Nothing was done. Ask her again if you still want it.' })
  }

  return (
    <div className={styles.dock}>
      <p className="visually-hidden" role="alert">
        {announcement}
      </p>
      {pending.map((confirmation) => (
        <Prompt
          key={confirmation.token}
          confirmation={confirmation}
          onUnseen={announce}
          onAnswer={(approved) => void reply(confirmation.token, approved)}
        />
      ))}
    </div>
  )
}

function Prompt({
  confirmation,
  onUnseen,
  onAnswer,
}: {
  confirmation: Confirmation
  onUnseen: (text: string) => void
  onAnswer: (approved: boolean) => void
}) {
  const card = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const bodyId = useId()
  const seconds = useSecondsLeft(confirmation.expiresAt)
  const dangerous = confirmation.risk === 'dangerous'
  // The ring starts where the request really is: a prompt restored after a reload
  // has already used part of its time.
  const [elapsed] = useState(() => Math.max(0, confirmation.expiresInMs - (confirmation.expiresAt - Date.now())))

  const request = confirmation.summary || capabilityRequest(confirmation.capability)
  // Announced whole when the card can't take the keyboard: the warning lives in its
  // description, which nobody hears unless the card is focused.
  const why = confirmation.fromReading ? FROM_READING : confirmation.afterWeb ? AFTER_WEB : ''
  const heard = why ? `${request}. ${why}` : request

  // A layout effect, so its cleanup runs while the card is still in the page: the
  // keyboard goes back to where it was instead of falling to the page body when
  // the card goes (answered, or run out).
  useLayoutEffect(() => {
    const element = card.current
    const active = document.activeElement
    const typing =
      active instanceof HTMLInputElement ||
      active instanceof HTMLTextAreaElement ||
      (active instanceof HTMLElement && active.isContentEditable)
    if (typing || !element) {
      onUnseen(heard)
      return
    }
    const before = active instanceof HTMLElement ? active : null
    element.focus()
    return () => {
      if (element.contains(document.activeElement) && before?.isConnected) before.focus()
    }
    // Runs once per card: a card's request never changes, and the announcer is stable.
  }, [onUnseen, heard])

  return (
    <div
      ref={card}
      tabIndex={-1}
      role="alertdialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      className={styles.prompt}
      onKeyDown={(event) => {
        if (event.key !== 'Escape') return
        event.stopPropagation()
        onAnswer(false)
      }}
    >
      <span className={styles.icon}>
        <ShieldQuestionMark size={20} aria-hidden="true" />
      </span>
      <div className={styles.text}>
        <h2 id={titleId} className={styles.title}>
          Can I go ahead?
        </h2>
        <p id={bodyId} className={styles.summary}>
          {request}
          <span className="visually-hidden">
            {`. ${why ? `${why} ` : ''}`}
            If you don't answer within {Math.round(confirmation.expiresInMs / 1000)} seconds, I won't.
          </span>
        </p>
        <div className={styles.meta}>
          <span>{capabilityRequest(confirmation.capability)}</span>
          {confirmation.fromReading && (
            <Badge tone="warning" size="sm">
              From something I read
            </Badge>
          )}
          {confirmation.afterWeb && (
            <Badge tone="warning" size="sm">
              After reading the web
            </Badge>
          )}
          {dangerous && (
            <Badge tone="danger" size="sm">
              High risk
            </Badge>
          )}
        </div>
      </div>
      <div className={styles.side}>
        <span className={styles.clock} aria-hidden="true">
          <Countdown durationMs={confirmation.expiresInMs} elapsedMs={elapsed} size={16} />
          <span className="num">{seconds} s</span>
        </span>
        <div className={styles.actions}>
          <Button size="sm" variant="secondary" onClick={() => onAnswer(false)}>
            Don't allow
          </Button>
          <Button size="sm" variant={dangerous ? 'danger' : 'primary'} onClick={() => onAnswer(true)}>
            Allow
          </Button>
        </div>
      </div>
    </div>
  )
}

/** Whole seconds until the deadline, ticking once a second, never below zero. */
function useSecondsLeft(deadline: number): number {
  const [seconds, setSeconds] = useState(() => secondsUntil(deadline))
  useEffect(() => {
    const tick = window.setInterval(() => setSeconds(secondsUntil(deadline)), 1000)
    return () => window.clearInterval(tick)
  }, [deadline])
  return seconds
}

function secondsUntil(deadline: number): number {
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
}
