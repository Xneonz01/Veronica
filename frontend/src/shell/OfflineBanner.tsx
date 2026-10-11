// Shown at the top of the content while the backend can't be reached: what is
// wrong and what to do, calmly. It goes by itself when the connection returns,
// and it never shows during the first connection, only after one was lost.

import { useLaneStatus } from '../audio/useAppEvents'
import { Notice } from '../ui/Feedback'
import styles from './AppShell.module.css'

export function OfflineBanner() {
  const status = useLaneStatus()
  if (status !== 'offline') return null
  return (
    <div className={styles.banner}>
      <Notice tone="attention" live title="Veronica's backend isn't answering">
        Start it, or give it a moment if it's restarting. Everything reconnects by itself, and
        nothing on this screen is lost.
      </Notice>
    </div>
  )
}
