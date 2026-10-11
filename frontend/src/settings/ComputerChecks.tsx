// "This computer" in About: whether it has what Veronica needs, checked when About opens
// and again on request. The backend looks and measures; the words are computerCopy.ts's.

import { CircleAlert, CircleCheck, CircleHelp, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Notice, Skeleton } from '../ui/Feedback'
import { useDoctor, type ComputerCheck, type Verdict } from '../system/useDoctor'
import styles from './ComputerChecks.module.css'
import { CHECKS, checkWords, summary } from './computerCopy'
import { Group, Row } from './Group'

const TITLE = 'This computer'
const LEAD = 'Whether this computer has what Veronica needs. Checking only looks: nothing is changed or downloaded.'
// A check that passed stays quiet, its emerald in the icon alone, so the one that needs
// attention is the one that reads first.
const TONE: Record<Verdict, BadgeTone> = { ok: 'neutral', warn: 'warning', fail: 'danger', unknown: 'neutral' }
const ICON = { ok: CircleCheck, warn: TriangleAlert, fail: CircleAlert, unknown: CircleHelp } as const
const TIME = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })

export function ComputerChecks() {
  const { checks, error, checking, checkedAt, check } = useDoctor()
  // Said once a check someone asked for is done. The first, on opening, is not announced,
  // nor is its failure: the version above says so already when the backend is away.
  const [said, setSaid] = useState('')
  const [asked, setAsked] = useState(false)
  // The button pressed goes away while it checks (disabled, or replaced by the rows or
  // the notice): focus comes back to the one in its place, unless it has moved on.
  const action = useRef<HTMLButtonElement>(null)
  const refocus = useRef(false)

  const again = async () => {
    setSaid('')
    setAsked(true)
    refocus.current = true
    const found = await check()
    if (found) setSaid(summary(found))
  }

  useEffect(() => {
    if (checking || !refocus.current) return
    refocus.current = false
    if (document.activeElement === null || document.activeElement === document.body) action.current?.focus()
  }, [checking])

  let content
  if (error !== null) {
    content = (
      <Notice
        tone="critical"
        live={asked}
        action={
          <Button ref={action} size="sm" loading={checking} onClick={() => void again()}>
            Try again
          </Button>
        }
      >
        {error}
      </Notice>
    )
  } else if (checks === null) {
    content = <Loading />
  } else {
    content = (
      <>
        {checks.map((item) => (
          <CheckRow key={item.check} item={item} />
        ))}
        <Row
          label={
            <span className={styles.when}>
              {checking ? 'Checking…' : checkedAt ? `Checked at ${TIME.format(checkedAt)}` : 'Checked'}
            </span>
          }
        >
          <Button ref={action} size="sm" loading={checking} onClick={() => void again()}>
            Check again
          </Button>
        </Row>
      </>
    )
  }

  return (
    <>
      <Group title={TITLE} lead={LEAD} bare={error !== null}>
        {content}
      </Group>
      <p className="visually-hidden" role="status">
        {said}
      </p>
    </>
  )
}

function CheckRow({ item }: { item: ComputerCheck }) {
  const label = CHECKS.get(item.check)
  if (label === undefined) return null // a check from a newer backend, without words here
  const { body, status } = checkWords(item)
  const Icon = ICON[item.verdict]
  const icon = <Icon size={12} aria-hidden="true" className={item.verdict === 'ok' ? styles.ready : undefined} />
  return (
    <Row label={label} description={body}>
      <Badge size="sm" tone={TONE[item.verdict]} icon={icon}>
        {status}
      </Badge>
    </Row>
  )
}

/** Shaped like the rows it becomes: one per check, and the line with Check again. */
function Loading() {
  return (
    <>
      {[...CHECKS.keys()].map((name, index) => (
        <div key={name} className={styles.skeleton} role={index === 0 ? 'status' : undefined}>
          {index === 0 && <span className="visually-hidden">Checking this computer</span>}
          <span className={styles.lines}>
            <Skeleton width={128} height={14} />
            <Skeleton width="72%" height={12} />
          </span>
          <Skeleton width={64} height={22} radius={11} />
        </div>
      ))}
      <div className={styles.skeleton}>
        <Skeleton width={112} height={12} />
        <Skeleton width={96} height={30} radius={8} />
      </div>
    </>
  )
}
