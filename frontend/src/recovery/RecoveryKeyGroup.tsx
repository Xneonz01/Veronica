// Settings > Privacy: her recovery key. Whether one is made and still covers all her
// data, making one (shown once), and checking the one saved. Where none can be made (her
// data locked here, or not encrypted on this system), the reason, never a dead button.

import { useState } from 'react'
import { Button } from '../ui/Button'
import { Notice, Skeleton } from '../ui/Feedback'
import { Group, Row } from '../settings/Group'
import { MakeRecoveryKey } from './MakeRecoveryKey'
import { ShownKey } from './ShownKey'
import { UseRecoveryKey } from './UseRecoveryKey'
import { useRecoveryStatus, type RecoveryStatus } from './useRecoveryStatus'

const LEAD =
  'Her memory and voice clips are encrypted with keys only this Windows account opens. A recovery key opens them ' +
  'on another Windows computer or account, or after Windows is reinstalled.'

const UNAVAILABLE: Record<NonNullable<RecoveryStatus['unavailable']>, string> = {
  unsupported: "Her data isn't encrypted on this system, so there is nothing for a recovery key to open.",
  locked:
    "Her memory or voices are locked on this computer, so a recovery key can't be made until they open. The " +
    'notice at the top of the window says what to do.',
  nothing: 'Nothing is encrypted yet, so there is nothing for a recovery key to open.',
}

export function RecoveryKeyGroup() {
  const { status, error, reload } = useRecoveryStatus()
  // Here, not in the row that made it: the status that follows swaps that row for another.
  const [shown, setShown] = useState<string | null>(null)
  function made(key: string) {
    setShown(key)
    void reload()
  }
  return (
    <>
      <Group title="Recovery key" lead={LEAD} bare={!status && error !== null}>
        {status ? (
          <Status status={status} onMade={made} />
        ) : error ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void reload()}>Try again</Button>}>
            {error}
          </Notice>
        ) : (
          <div role="status">
            <span className="visually-hidden">Reading whether she has a recovery key</span>
            <Skeleton width={260} />
          </div>
        )}
      </Group>
      <ShownKey key={shown ?? ''} recoveryKey={shown} onDone={() => setShown(null)} />
    </>
  )
}

function Status({ status, onMade }: { status: RecoveryStatus; onMade: (key: string) => void }) {
  if (status.unavailable !== null) {
    return <Row label="Not available" description={UNAVAILABLE[status.unavailable]}>{null}</Row>
  }
  if (status.partial) {
    return (
      <Row
        label="Out of date"
        description="Some of her data has a newer key than your recovery key opens. Make a new one to cover it all."
      >
        <MakeRecoveryKey replacing onMade={onMade} />
      </Row>
    )
  }
  if (status.made !== null) {
    return (
      <Row
        label={madeOn(status.made)}
        description="It opens her memory and voices on another Windows computer or account. If you've lost it, make a new one: the old one stops working."
      >
        <UseRecoveryKey mode="check" />
        <MakeRecoveryKey replacing onMade={onMade} />
      </Row>
    )
  }
  return (
    <Row
      label="None yet"
      description="Without one, her memory and voices open only in this Windows account, on this computer."
    >
      <MakeRecoveryKey replacing={false} onMade={onMade} />
    </Row>
  )
}

/** When it was made, in this computer's time zone: to the minute, so a key made by
    something else, the same day, shows as a change. */
function madeOn(made: string): string {
  const when = new Date(made)
  if (Number.isNaN(when.getTime())) return 'Made'
  const day = when.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
  const time = when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return `Made on ${day} at ${time}`
}
