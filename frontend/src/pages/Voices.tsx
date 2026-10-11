// The voice studio: the voices she can speak in, one of them in use, and the
// microphone and speakers she uses. A voice added, switched or deleted anywhere
// arrives over the event lane, so the page never needs a refresh.

import { AudioWaveform, Plus } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { useVoices, type VoiceInfo } from '../audio/useVoices'
import { AudioDevices } from '../call/AudioDevices'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { EmptyState, Notice, Skeleton } from '../ui/Feedback'
import { AddVoiceSheet } from '../voices/AddVoiceSheet'
import { VoiceCard } from '../voices/VoiceCard'
import styles from './Voices.module.css'

export function Voices() {
  const { voices, loading, error, setError, refresh, activate, create, remove, preview } = useVoices()
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<VoiceInfo | null>(null)
  // No cloned voice in use means she is speaking in her own.
  const builtIn = !voices.some((voice) => voice.active)

  // Every (re)connect refreshes, so the list recovers after a backend restart.
  useAppEvents({
    onVoicesUpdated: refresh,
    onVoiceChanged: () => void refresh(),
    onConnected: () => void refresh(),
  })

  const hear = useCallback(
    async (id: string) => {
      try {
        await preview(id)
      } catch (failure) {
        setError({ kind: 'preview_failed', severity: 'recoverable', message: messageFor(failure, "Couldn't play a sample. Try again.") })
      }
    },
    [preview, setError],
  )

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.titles}>
          <h1 className={styles.title}>Voices</h1>
          <p className={styles.lead}>The voice she speaks in. Clone one from clips of a person who agreed to it; it stays on this computer.</p>
        </div>
        <Button variant="primary" icon={<Plus size={16} aria-hidden="true" />} onClick={() => setAdding(true)}>
          Add a voice
        </Button>
      </header>

      <div className={styles.body}>
        {error && (
          <Notice
            tone={error.severity === 'fatal' ? 'critical' : 'attention'}
            live
            action={
              <Button size="sm" variant="secondary" onClick={() => (error.severity === 'fatal' ? void refresh() : setError(null))}>
                {error.severity === 'fatal' ? 'Try again' : 'OK'}
              </Button>
            }
          >
            {error.message}
          </Notice>
        )}

        {loading ? (
          <div className={styles.grid} role="status">
            <span className="visually-hidden">Loading voices</span>
            {[0, 1, 2].map((index) => (
              <div key={index} className={styles.skeleton}>
                <Skeleton height={36} />
                <Skeleton width="50%" />
                <Skeleton width="30%" height={11} />
              </div>
            ))}
          </div>
        ) : voices.length > 0 ? (
          <div className={styles.voices}>
            {builtIn && <p className={styles.inUse}>She's speaking in her built-in voice. Use one of these to change that.</p>}
            <div className={styles.grid}>
              {voices.map((voice) => (
                <VoiceCard key={voice.voice_id} voice={voice} onUse={(id) => void activate(id)} onHear={hear} onDelete={setDeleting} />
              ))}
            </div>
          </div>
        ) : (
          !error && (
            <EmptyState
              icon={<AudioWaveform size={22} aria-hidden="true" />}
              title="Her own voice, for now"
              action={
                <Button variant="primary" onClick={() => setAdding(true)}>
                  Add a voice
                </Button>
              }
            >
              She speaks in her built-in voice. Add clips of someone who agreed to it, and she can speak in theirs.
            </EmptyState>
          )
        )}

        <section className={styles.devices} aria-labelledby="voices-devices">
          <div className={styles.sectionHead}>
            <h2 id="voices-devices" className={styles.sectionTitle}>
              Microphone and speakers
            </h2>
            <p className={styles.sectionLead}>Used for calls and for recording clips. A change applies at once, even mid-call.</p>
          </div>
          <AudioDevices />
        </section>
      </div>

      <AddVoiceSheet open={adding} onClose={() => setAdding(false)} onCreate={create} />

      <Confirm
        open={deleting !== null}
        title="Delete this voice?"
        confirmLabel="Delete"
        cancelLabel="Keep it"
        destructive
        onConfirm={() => {
          if (deleting) void remove(deleting.voice_id)
          setDeleting(null)
        }}
        onCancel={() => setDeleting(null)}
      >
        “{deleting?.name}” is removed from this computer.
        {deleting?.active && ' She goes back to her built-in voice.'}
      </Confirm>
    </div>
  )
}
