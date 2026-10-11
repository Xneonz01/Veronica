// A new recovery key, shown this once: to copy or write down. The sheet can't be closed
// until the person says it is saved, since nothing can show it again; the browser may
// close it on a second Escape, and `Sheet` shows it again. The key lives only in the
// owner's state, and goes when the sheet does; the owner mounts this afresh for each key
// (`key`), so nothing ticked for one carries to the next. Copied, it is cleared from the
// clipboard on Done.

import { Check, Copy } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Sheet } from '../ui/Sheet'
import styles from './Recovery.module.css'

const COPY_FAILED = "Couldn't copy it. Select the key and copy it, or write it down."

export function ShownKey({ recoveryKey, onDone }: { recoveryKey: string | null; onDone: () => void }) {
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState<boolean | null>(null)
  // The keyboard lands on Copy: the header's Close does nothing until it is saved.
  const copyButton = useRef<HTMLButtonElement>(null)

  function done() {
    if (copied) void navigator.clipboard.writeText('').catch(() => undefined)
    onDone()
  }

  async function copy() {
    if (recoveryKey === null) return
    try {
      await navigator.clipboard.writeText(recoveryKey)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Sheet
      open={recoveryKey !== null}
      onClose={() => {
        if (saved) done()
      }}
      title="Save your recovery key"
      initialFocus={copyButton}
      footer={
        <Button variant="primary" onClick={done} disabled={!saved}>
          Done
        </Button>
      }
    >
      <div className={styles.shown}>
        <p className={styles.body}>
          It opens her memory and voices on another Windows computer or account, or after Windows is reinstalled.
          Keep it somewhere safe, away from this computer: in a password manager, or on paper.
        </p>
        <div className={styles.keyLine}>
          <p className={styles.key}>{recoveryKey}</p>
          <Button
            ref={copyButton}
            size="sm"
            icon={copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
            onClick={() => void copy()}
          >
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        {copied === false && (
          <p className={styles.error} role="alert">
            {COPY_FAILED}
          </p>
        )}
        <p className={styles.warning}>
          Anyone with this key and a copy of her data folder can read her memory. It won't be shown again.
        </p>
        <Checkbox label="I've saved my recovery key" checked={saved} onChange={setSaved} />
      </div>
    </Sheet>
  )
}
