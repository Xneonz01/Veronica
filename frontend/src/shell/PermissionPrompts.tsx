// Her requests for permission, over whatever page is open. A request she can't
// act on without you is never a toast: it stays until you answer or it runs out,
// and running out means no.
//
// It takes the keyboard only when nothing is being typed. An Enter meant for the
// composer must never land on Allow, and neither may a click already on its way when
// the card appears under it: Allow wakes up after ARM_MS.
//
// What it shows can be an attacker's text, so it is shown as it is: characters that
// render as nothing are marked where they sit, and an international address is named
// as it really resolves.

import { ShieldQuestionMark } from 'lucide-react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { useConfirmations, type AskReason, type Confirmation } from '../kernel/useConfirmations'
import { capabilityRequest } from '../lib/capabilities'
import { REVEALED, reveal } from '../lib/reveal'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Countdown } from '../ui/Countdown'
import styles from './PermissionPrompts.module.css'
import { PromptDetails } from './PromptDetails'
import { useToasts } from './toast'

export const ARM_MS = 500

// Why she asks for something its setting would have let run. The words say where the
// request came from, which is all the check knows, and not who wanted it done.
const REASONS: Record<AskReason, { badge: string; said: string }> = {
  from_reading: { badge: 'From something I read', said: 'The words for this came from something I read, not from you.' },
  after_web: { badge: 'After reading the web', said: "I read something on the web just before this, so I'm checking with you." },
  after_mcp: { badge: 'After a tool server answered', said: "A tool server answered me just before this, so I'm checking with you." },
  unfamiliar_link: { badge: 'Address not from you', said: "You didn't give me this address, and no search turned it up." },
  sends_secret: { badge: 'Looks like a secret', said: 'This would send something that looks like a password or a key.' },
  sends_private: { badge: 'Sends something of yours', said: 'This would send words from your files or from what I know about you.' },
}
const HIDDEN = "Some of this is written in characters that don't show on screen. I've marked them."

export function PermissionPrompts() {
  const { pending, answer, fullValues } = useConfirmations()
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
          fullValues={fullValues}
          onUnseen={announce}
          onAnswer={(approved) => void reply(confirmation.token, approved)}
        />
      ))}
    </div>
  )
}

function Prompt({
  confirmation,
  fullValues,
  onUnseen,
  onAnswer,
}: {
  confirmation: Confirmation
  fullValues: (token: string) => Promise<Record<string, string> | null>
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

  const armed = useArmed()

  const request = reveal(confirmation.summary) || capabilityRequest(confirmation.capability)
  const reason = confirmation.why ? REASONS[confirmation.why] : null
  const hidden = REVEALED.test(request) || confirmation.details.some((detail) => REVEALED.test(detail.value))
  const { target, targetReadAs } = confirmation
  const address = targetReadAs ? `${targetReadAs} is an international name. Its real address is ${target}.` : ''
  // Announced whole when the card can't take the keyboard: the warnings live in its
  // description, which nobody hears unless the card is focused.
  const warnings = [reason?.said, hidden ? HIDDEN : '', address].filter(Boolean).join(' ')
  const heard = warnings ? `${request}. ${warnings}` : request

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
        <div id={bodyId} className={styles.body}>
          <p className={styles.summary}>{request}</p>
          {warnings && <p className={styles.warning}>{warnings}</p>}
          <span className="visually-hidden">
            If you don't answer within {Math.round(confirmation.expiresInMs / 1000)} seconds, I won't.
          </span>
        </div>
        <PromptDetails confirmation={confirmation} fullValues={fullValues} />
        <div className={styles.meta}>
          <span>{capabilityRequest(confirmation.capability)}</span>
          {reason && (
            <Badge tone="warning" size="sm">
              {reason.badge}
            </Badge>
          )}
          {hidden && (
            <Badge tone="warning" size="sm">
              Hidden characters
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
          <Button size="sm" variant={dangerous ? 'danger' : 'primary'} disabled={!armed} onClick={() => onAnswer(true)}>
            Allow
          </Button>
        </div>
      </div>
    </div>
  )
}

/** False for the first ARM_MS after the card appears: a double click, or a click aimed
 *  at what was there before, never lands on Allow. */
function useArmed(): boolean {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    const wake = window.setTimeout(() => setArmed(true), ARM_MS)
    return () => window.clearTimeout(wake)
  }, [])
  return armed
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
