// Making her recovery key: asked first, since a new one stops the one before from
// working. The new key goes to `onMade`, to be shown once by the group, which stays when
// this row gives way to the one for a key made. The backend never keeps it
// (`POST /recovery-key`).

import { useState } from 'react'
import { apiFetch, messageFor } from '../lib/api'
import { isRecord } from '../shell/useProblems'
import { Button } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { refusalWords } from './recoveryKey'

const FAILED = "Couldn't make a recovery key. Try again in a moment."

export function MakeRecoveryKey({ replacing, onMade }: { replacing: boolean; onMade: (key: string) => void }) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function ask() {
    setError(null)
    setAsking(true)
  }

  async function make() {
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/recovery-key', { method: 'POST' })
      if (!response.ok) {
        setError(await refusalWords(response, FAILED))
        return
      }
      const body: unknown = await response.json()
      if (!isRecord(body) || typeof body.key !== 'string') {
        setError(FAILED)
        return
      }
      setAsking(false)
      onMade(body.key)
    } catch (err) {
      setError(messageFor(err, FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size="sm" onClick={ask}>
        {replacing ? 'Make a new one' : 'Make a recovery key'}
      </Button>
      <Confirm
        open={asking}
        title={replacing ? 'Make a new recovery key?' : 'Make a recovery key?'}
        confirmLabel={replacing ? 'Make a new key' : 'Make a recovery key'}
        cancelLabel={replacing ? 'Keep the one I have' : 'Not now'}
        onConfirm={() => void make()}
        onCancel={() => setAsking(false)}
        busy={busy}
        error={error}
      >
        {replacing
          ? 'The recovery key you saved stops working: only the new one will open her memory and voices.'
          : "You'll see it once. Keep it somewhere safe: with it, her memory and voices open on another Windows computer or account, or after Windows is reinstalled."}
      </Confirm>
    </>
  )
}
