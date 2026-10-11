// Adding a voice: a name, clips of one person speaking (uploaded, or recorded
// here), and your word that you have the right to use that voice. She needs at
// least 6 seconds of clear speech and uses up to 20; the new voice goes into use
// when it is ready.

import { FileAudio } from 'lucide-react'
import { useRef, useState } from 'react'
import { useRecorder } from '../audio/useRecorder'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Notice, Working } from '../ui/Feedback'
import { TextField } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { Sheet } from '../ui/Sheet'
import styles from './AddVoiceSheet.module.css'
import { ClipRecorder } from './ClipRecorder'

type Way = 'upload' | 'record'

export function AddVoiceSheet({
  open,
  onClose,
  onCreate,
}: {
  open: boolean
  onClose: () => void
  onCreate: (name: string, files: File[], source: Way) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [way, setWay] = useState<Way>('upload')
  const [uploads, setUploads] = useState<File[]>([])
  const [recorded, setRecorded] = useState<{ wav: Blob; seconds: number } | null>(null)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const nameField = useRef<HTMLInputElement>(null)
  const recorder = useRecorder()

  const clips = way === 'upload' ? uploads : recorded ? [new File([recorded.wav], 'recording.wav', { type: 'audio/wav' })] : []
  const ready = name.trim() !== '' && consent && clips.length > 0 && !recorder.recording

  function close() {
    if (busy) return
    // Also stops a recording still waiting for the microphone, which `recording`
    // doesn't show yet: it would otherwise start behind the closed sheet.
    recorder.cancel()
    onClose()
  }

  function chooseWay(next: Way) {
    recorder.cancel() // a take in progress belongs to the way being left
    setWay(next)
  }

  async function create() {
    setBusy(true)
    setFailed(null)
    try {
      await onCreate(name.trim(), clips, way)
      setName('')
      setUploads([])
      setRecorded(null)
      setConsent(false)
      onClose()
    } catch (error) {
      setFailed(messageFor(error, "Couldn't make that voice. Try other clips, or record again."))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={close}
      title="Add a voice"
      description="Clips of one person speaking clearly: at least 6 seconds, and up to 20 is used."
      initialFocus={nameField}
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void create()} disabled={!ready} loading={busy}>
            Make this voice
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <TextField ref={nameField} label="Name" placeholder="Rowan, reading" value={name} onChange={(event) => setName(event.target.value)} />
        <Segmented<Way>
          label="Clips"
          value={way}
          onChange={chooseWay}
          options={[
            { value: 'upload', label: 'Upload clips' },
            { value: 'record', label: 'Record here' },
          ]}
        />
        {way === 'upload' ? (
          <div className={styles.clips}>
            <input
              ref={picker}
              type="file"
              accept="audio/*"
              multiple
              hidden
              onChange={(event) => setUploads(Array.from(event.target.files ?? []))}
            />
            <button type="button" className={styles.drop} onClick={() => picker.current?.click()}>
              <FileAudio size={22} aria-hidden="true" />
              {uploads.length > 0 ? (
                <span>
                  {uploads.length === 1 ? uploads[0].name : `${uploads.length} clips chosen`} · choose again
                </span>
              ) : (
                <span>Choose audio files</span>
              )}
            </button>
          </div>
        ) : (
          <div className={styles.clips}>
            <ClipRecorder recorder={recorder} onComplete={(wav, seconds) => setRecorded({ wav, seconds })} onError={setFailed} />
            {recorded && !recorder.recording && <p className={styles.recorded}>Recorded {recorded.seconds.toFixed(1)} seconds. Record again to replace it.</p>}
          </div>
        )}
        <Checkbox
          label="I have the right to use this voice"
          description="It is your own, or its owner agreed to it being cloned."
          checked={consent}
          onChange={setConsent}
        />
        {busy && <Working>Building the voice from your clips. This takes a few seconds.</Working>}
        {failed && (
          <Notice tone="critical" live>
            {failed}
          </Notice>
        )}
      </div>
    </Sheet>
  )
}
