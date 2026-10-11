// Using her recovery key: from the notice that her memory or voices are locked, to open
// them for this Windows account, and from Settings, to check the one saved still fits.
// The backend reads the key and its check (`POST /recovery-key/use`); the field only
// catches early what can't be one. What it opens is used from the next start, which the
// notice at the top of the window then says.

import { useId, useState, type FormEvent } from 'react'
import { apiFetch, messageFor } from '../lib/api'
import { useToasts } from '../shell/toast'
import { isRecord } from '../shell/useProblems'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { TextField } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import styles from './Recovery.module.css'
import { keyProblem, refusalWords, storesNamed } from './recoveryKey'

const FAILED = "Couldn't read that recovery key. Try again in a moment."

type Used = { restored: string[]; opened: string[]; unmatched: string[] }

const WORDS = {
  use: {
    button: 'Use recovery key',
    title: 'Use your recovery key',
    lead: 'Enter the recovery key you saved. It opens her memory and voices for this Windows account.',
    submit: 'Open her data',
  },
  check: {
    button: 'Check a key',
    title: 'Check a recovery key',
    lead: 'Enter the recovery key you saved to check that it still opens her memory and voices.',
    submit: 'Check',
  },
} as const

export function UseRecoveryKey({ mode }: { mode: keyof typeof WORDS }) {
  const words = WORDS[mode]
  // Its own: both notices, memory's and voices', can offer one, and the footer's button
  // finds its form by id.
  const formId = useId()
  const toasts = useToasts()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fits, setFits] = useState<string | null>(null)

  function close() {
    setOpen(false)
    setText('')
    setTried(false)
    setError(null)
    setFits(null)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setTried(true)
    if (keyProblem(text) !== null) return
    setBusy(true)
    setError(null)
    try {
      const response = await apiFetch('/recovery-key/use', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: text }),
      })
      if (!response.ok) {
        setError(await refusalWords(response, FAILED))
        return
      }
      const used = readUsed(await response.json())
      if (used.restored.length > 0) {
        close()
        toasts.show({
          tone: 'positive',
          title: `Your recovery key opened ${storesNamed(used.restored)}`,
          body: `Restart Veronica's backend to use ${oneThing(used.restored) ? 'it' : 'them'} on this computer.`,
        })
        return
      }
      setFits(fitWords(used))
    } catch (err) {
      setError(messageFor(err, FAILED))
    } finally {
      setBusy(false)
    }
  }

  const problem = tried ? keyProblem(text) : null
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        {words.button}
      </Button>
      <Sheet
        open={open}
        onClose={busy ? () => undefined : close}
        title={words.title}
        size="sm"
        footer={
          fits !== null ? (
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="secondary" onClick={close} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" form={formId} loading={busy}>
                {words.submit}
              </Button>
            </>
          )
        }
      >
        <form id={formId} className={styles.form} onSubmit={(event) => void submit(event)} noValidate>
          <p className={styles.body}>{words.lead}</p>
          <TextField
            label="Recovery key"
            className={styles.input}
            value={text}
            onChange={(event) => {
              setText(event.target.value)
              setError(null)
              setFits(null)
            }}
            placeholder="XXXX-XXXX-XXXX-…"
            hint="36 letters and digits, in groups of four."
            error={problem}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={200}
          />
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {fits !== null && (
            <Notice tone="positive" live>
              {fits}
            </Notice>
          )}
        </form>
      </Sheet>
    </>
  )
}

function readUsed(body: unknown): Used {
  const list = (value: unknown) => (Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [])
  return isRecord(body)
    ? { restored: list(body.restored), opened: list(body.opened), unmatched: list(body.unmatched) }
    : { restored: [], opened: [], unmatched: [] }
}

/** What a key that fits says, when nothing needed opening. */
function fitWords({ opened, unmatched }: Used): string {
  const fits = `That's her recovery key: it opens ${storesNamed(opened)}.`
  if (unmatched.length === 0) return fits
  const verb = oneThing(unmatched) ? 'has' : 'have'
  return `${fits} ${capitalised(storesNamed(unmatched))} ${verb} a newer key it doesn't open: make a new recovery key to cover everything.`
}

/** Her memory is one thing ("it", "has"); her voices are many. */
function oneThing(stores: readonly string[]): boolean {
  return stores.length === 1 && stores[0] === 'memory'
}

function capitalised(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
