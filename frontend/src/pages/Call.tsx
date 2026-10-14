// The call page: her orb at the centre, what each of you is saying, and the
// controls. The call itself lives in the shell (call/CallProvider.tsx), so leaving
// this page doesn't end it; the sidebar keeps a dock for it.

import { Mic, MicOff, Phone, PhoneOff, SlidersHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useLaneStatus } from '../audio/useAppEvents'
import { AudioDevices } from '../call/AudioDevices'
import { STATE_LABEL } from '../call/callCopy'
import { useCall, useCallStatus } from '../call/context'
import { ExpressiveChoice } from '../call/ExpressiveChoice'
import { GreetingChoice } from '../call/GreetingChoice'
import { LiveLines } from '../call/LiveLines'
import { Orb } from '../call/Orb'
import { useElapsed } from '../call/useElapsed'
import { Button, IconButton } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { Sheet } from '../ui/Sheet'
import styles from './Call.module.css'

export function Call() {
  const call = useCall()
  const status = useCallStatus()
  const clock = useElapsed(status.since)
  const [settings, setSettings] = useState(false)
  const connecting = call.state === 'connecting' || call.state === 'warming_up'
  const talked = Boolean(call.finalText || call.partial || call.reply)
  // With her backend down a call can only fail, so it isn't offered until it is back.
  const unreachable = useLaneStatus() === 'offline' && !call.inCall && !connecting

  // "Start a call" from the palette arrives here and starts it, once per arrival.
  const location = useLocation()
  const navigate = useNavigate()
  const started = useRef<string | null>(null)
  const { startCall } = call
  useEffect(() => {
    if (!(location.state as { startCall?: boolean } | null)?.startCall || started.current === location.key) return
    started.current = location.key
    void startCall()
    void navigate(location.pathname, { replace: true, state: null })
  }, [location, startCall, navigate])

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>Call</h1>
        {clock && <span className={`${styles.clock} num`}>{clock}</span>}
        <IconButton
          className={styles.settingsButton}
          label="Call settings"
          icon={<SlidersHorizontal size={18} aria-hidden="true" />}
          onClick={() => setSettings(true)}
        />
      </header>

      <div className={styles.body}>
        <div className={styles.stage}>
          <Orb state={call.state} amplitude={call.amplitude} ripple={status.ripple} size={260} />
          <p className={styles.state} aria-live="polite">
            {unreachable && call.state === 'idle' ? "She can't take a call yet" : STATE_LABEL[call.state]}
          </p>
          {call.state === 'warming_up' && (
            <p className={styles.hint}>The first call after Veronica starts loads her voice, which takes about a minute.</p>
          )}
          {call.state === 'idle' && !talked && (
            <p className={styles.hint}>
              {unreachable
                ? "Her backend isn't answering. The call button comes back the moment it is."
                : 'Talk to her out loud. She listens until you end the call, and you can talk over her to interrupt.'}
            </p>
          )}
        </div>

        {call.error && (
          <Notice tone={call.error.severity === 'fatal' ? 'critical' : 'attention'} live>
            {call.error.message}
          </Notice>
        )}

        {(call.inCall || talked) && (
          <LiveLines
            finalText={call.finalText}
            partial={call.partial}
            reply={call.reply}
            caveat={call.caveat}
            state={call.state}
          />
        )}

        <div className={styles.controls}>
          {call.inCall ? (
            <>
              <Button
                size="lg"
                variant="secondary"
                aria-pressed={call.muted}
                icon={call.muted ? <MicOff size={18} aria-hidden="true" /> : <Mic size={18} aria-hidden="true" />}
                onClick={call.toggleMute}
              >
                {call.muted ? 'Unmute' : 'Mute'}
              </Button>
              <Button size="lg" variant="danger" icon={<PhoneOff size={18} aria-hidden="true" />} onClick={call.endCall}>
                End call
              </Button>
            </>
          ) : (
            <Button
              size="lg"
              variant="primary"
              icon={<Phone size={18} aria-hidden="true" />}
              loading={connecting}
              disabled={unreachable}
              onClick={() => void call.startCall()}
            >
              {call.state === 'error' ? 'Call again' : connecting ? 'Connecting' : 'Start a call'}
            </Button>
          )}
        </div>

        {call.metrics && (
          <p className={styles.latency}>
            Last reply: first audio after{' '}
            <span className="num">{seconds(call.metrics.firstAudioMs)}</span>, finished after{' '}
            <span className="num">{seconds(call.metrics.totalMs)}</span>
          </p>
        )}
      </div>

      <Sheet open={settings} onClose={() => setSettings(false)} title="Call settings" size="md">
        <div className={styles.sheet}>
          <section className={styles.group} aria-labelledby="call-audio">
            <h3 id="call-audio" className={styles.groupTitle}>
              Microphone and speakers
            </h3>
            <AudioDevices active={settings} />
          </section>
          <section className={styles.group} aria-labelledby="call-greeting">
            <h3 id="call-greeting" className={styles.groupTitle}>
              Greeting
            </h3>
            <GreetingChoice />
          </section>
          <section className={styles.group} aria-labelledby="call-voice">
            <h3 id="call-voice" className={styles.groupTitle}>
              Her voice
            </h3>
            <ExpressiveChoice />
          </section>
        </div>
      </Sheet>
    </div>
  )
}

function seconds(milliseconds: number | null): string {
  return milliseconds === null ? '…' : `${(milliseconds / 1000).toFixed(2)} s`
}
