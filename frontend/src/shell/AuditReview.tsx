// The banner's one action: an activity log changed outside Veronica, accepted as it is
// once the person has looked (`POST /audit/review`). It stops a change being reported,
// so it is asked for twice; the log records the review like any other entry, and still
// catches any change made after it.

import { useState } from 'react'
import { apiFetch, failureMessage, messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { useToasts } from './toast'
import { isRecord } from './useProblems'

const FAILED = "Couldn't mark it as reviewed. Try again in a moment."
// The walk after the review still found a change: one made while it ran.
const CHANGED_AGAIN = 'The log changed again while she checked it. Try again.'

export function AuditReview({ onReviewed }: { onReviewed: () => void }) {
  const toasts = useToasts()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function ask() {
    setError(null)
    setOpen(true)
  }

  async function review() {
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/audit/review', { method: 'POST' })
      if (!response.ok) {
        setError(await failureMessage(response, FAILED))
        return
      }
      const body: unknown = await response.json()
      if (!isRecord(body) || body.ok !== true) {
        setError(CHANGED_AGAIN)
        return
      }
      setOpen(false)
      toasts.show({ tone: 'positive', title: 'Marked as reviewed', body: "She'll still catch any change made from now on." })
      onReviewed()
    } catch (err) {
      setError(messageFor(err, FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" onClick={ask}>
        Mark as reviewed
      </Button>
      <Confirm
        open={open}
        title="Mark these changes as reviewed?"
        confirmLabel="Mark as reviewed"
        cancelLabel="Not now"
        onConfirm={() => void review()}
        onCancel={() => setOpen(false)}
        busy={busy}
        error={error}
      >
        She'll accept her activity log as it is now and stop reporting these changes. Any change made after this
        is still caught, and the review itself is added to the log.
      </Confirm>
    </>
  )
}
