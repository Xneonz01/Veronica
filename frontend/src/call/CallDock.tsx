// The call, while you are somewhere else: what she is doing, how long you have
// been talking, and the two controls you need without going back. It sits in the
// sidebar above its footer, the way a voice channel does in a chat app; in the rail
// it shrinks to a live dot and the end button.

import { Mic, MicOff, PhoneOff } from 'lucide-react'
import { useContext } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ROUTES } from '../lib/routes'
import { RailContext } from '../shell/rail'
import { IconButton } from '../ui/Button'
import { cx } from '../ui/cx'
import styles from './CallDock.module.css'
import { STATE_LABEL } from './callCopy'
import { useCallStatus } from './context'
import { useElapsed } from './useElapsed'

export function CallDock() {
  const status = useCallStatus()
  const rail = useContext(RailContext)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const clock = useElapsed(status.since)
  if (!status.inCall || pathname === ROUTES.call) return null

  const label = `${STATE_LABEL[status.state]}${clock ? `, ${clock}` : ''}`
  return (
    <section className={cx(styles.dock, rail && styles.rail)} aria-label="Call in progress">
      <button type="button" className={styles.open} onClick={() => navigate(ROUTES.call)} title={rail ? label : 'Back to the call'}>
        <span className={styles.dot} data-state={status.state} aria-hidden="true" />
        {rail ? (
          <span className="visually-hidden">Back to the call: {label}</span>
        ) : (
          <span className={styles.text}>
            <span className={styles.title}>On a call</span>
            <span className={styles.meta}>
              {STATE_LABEL[status.state]}
              {clock && <span className="num"> · {clock}</span>}
            </span>
          </span>
        )}
      </button>
      {!rail && (
        <IconButton
          size="sm"
          label={status.muted ? 'Unmute microphone' : 'Mute microphone'}
          pressed={status.muted}
          icon={status.muted ? <MicOff size={16} aria-hidden="true" /> : <Mic size={16} aria-hidden="true" />}
          onClick={status.toggleMute}
        />
      )}
      <IconButton size="sm" variant="danger" label="End the call" icon={<PhoneOff size={16} aria-hidden="true" />} onClick={status.endCall} />
    </section>
  )
}
