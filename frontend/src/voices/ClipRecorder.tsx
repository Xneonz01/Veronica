// Recording a voice clip here: a record button, the time so far, and a live level
// so you can see you are being heard. The level is drawn from the audio loop, not
// React state, so recording re-renders nothing per frame.

import { Circle, Square } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { readRms } from '../audio/useAmplitude'
import type { Recorder } from '../audio/useRecorder'
import { Button } from '../ui/Button'
import styles from './ClipRecorder.module.css'

export function ClipRecorder({
  recorder,
  onComplete,
  onError,
}: {
  recorder: Recorder
  onComplete: (wav: Blob, seconds: number) => void
  onError: (message: string) => void
}) {
  const bar = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const element = bar.current
    if (!recorder.recording || !element) return
    let frame = 0
    const buffer = new Float32Array(1024)
    const loop = () => {
      element.style.transform = `scaleX(${Math.min(1, readRms(recorder.analyser.current, buffer) * 4)})`
      frame = requestAnimationFrame(loop)
    }
    frame = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(frame)
      element.style.transform = 'scaleX(0)'
    }
  }, [recorder.recording, recorder.analyser])

  async function toggle() {
    if (recorder.recording) {
      const wav = recorder.stop()
      if (wav) onComplete(wav, recorder.seconds)
      else onError('That recording came out empty. Try again, a little closer to the microphone.')
      return
    }
    try {
      await recorder.start()
    } catch (error) {
      const refused = error instanceof DOMException && error.name === 'NotAllowedError'
      onError(
        refused
          ? "The microphone is blocked. Allow it for Veronica in your system's privacy settings."
          : "Couldn't start recording. Check that a microphone is connected.",
      )
    }
  }

  return (
    <div className={styles.recorder}>
      <Button
        variant={recorder.recording ? 'danger' : 'secondary'}
        icon={recorder.recording ? <Square size={12} fill="currentColor" aria-hidden="true" /> : <Circle size={12} fill="currentColor" aria-hidden="true" />}
        onClick={() => void toggle()}
      >
        {recorder.recording ? 'Stop' : 'Record'}
      </Button>
      <span className={`${styles.time} num`} aria-live="off">
        {recorder.seconds.toFixed(1)} s
      </span>
      <span className={styles.track} aria-hidden="true">
        <span ref={bar} className={styles.bar} />
      </span>
    </div>
  )
}
