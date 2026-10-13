// What a permission request would send or write, argument by argument, as it is. The
// line above names the action; this is the part an attacker would hide in: the rest of
// an address after its host, the body of a file. Nothing is cut or blanked silently: a
// value cut short says how much is left, a secret blanked says how much it hid, and the
// whole of it is one click away, so nothing can hide behind a secret from you.

import { memo, useMemo, useState } from 'react'
import type { Confirmation } from '../kernel/useConfirmations'
import { reveal } from '../lib/reveal'
import { Button } from '../ui/Button'
import styles from './PromptDetails.module.css'

// The arguments her own tools take, in words; any other is shown by its own name.
const LABELS: Record<string, string> = {
  url: 'Address',
  query: 'Search',
  path: 'File',
  content: 'Text',
  name: 'App',
  kind: 'Kind',
  old: 'Instead of',
  new: 'Now',
  overwrite: 'Replace if it exists',
  in_content: 'Inside files',
}
// Yes or no, where the argument is one: "Replace if it exists: Yes" is the line to read.
const SWITCHES = new Set(['overwrite', 'in_content'])
const ANSWER: Record<string, string> = { true: 'Yes', false: 'No' }

/** Whether the line above already shows `value` whole: a sentence's full stop aside,
 *  which a summary leaves off its quote. */
function shownAbove(summary: string, value: string): boolean {
  return summary.includes(value.replace(/[.!?]+$/, ''))
}

type Loading = 'idle' | 'loading' | 'failed'

export const PromptDetails = memo(function PromptDetails({
  confirmation,
  fullValues,
}: {
  confirmation: Confirmation
  fullValues: (token: string) => Promise<Record<string, string> | null>
}) {
  const [full, setFull] = useState<Record<string, string> | null>(null)
  const [loading, setLoading] = useState<Loading>('idle')
  // A value the line above already shows whole isn't repeated.
  const rows = useMemo(
    () =>
      confirmation.details
        .filter((detail) => detail.more > 0 || detail.masked > 0 || !shownAbove(confirmation.summary, detail.value))
        .map((detail) => {
          const value = reveal(full?.[detail.field] ?? detail.value)
          const cut = !full && detail.more > 0
          return { ...detail, value: SWITCHES.has(detail.field) ? (ANSWER[value] ?? value) : value, cut, blanked: !full && detail.masked > 0 }
        }),
    [confirmation, full],
  )
  if (rows.length === 0) return null

  async function showAll() {
    setLoading('loading')
    const values = await fullValues(confirmation.token)
    setFull(values)
    setLoading(values ? 'idle' : 'failed')
  }

  return (
    <div className={styles.details}>
      <dl className={styles.list}>
        {rows.map((row) => (
          <div key={row.field} className={styles.row}>
            <dt className={styles.field}>{LABELS[row.field] ?? row.field}</dt>
            <dd className={styles.value}>
              {row.value}
              {row.cut && <span className={styles.more}>{` … ${row.more.toLocaleString()} more characters`}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {rows.some((row) => row.cut || row.blanked) && (
        <Button size="sm" variant="quiet" loading={loading === 'loading'} onClick={() => void showAll()}>
          Show all
        </Button>
      )}
      {loading === 'failed' && (
        <p role="alert" className={styles.failed}>
          I couldn't get the rest of it. The request may have run out.
        </p>
      )}
    </div>
  )
})
