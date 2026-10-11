// Which microphone she hears and which speakers she talks through, with a level
// meter to check the microphone before a call. A change made here, on any page,
// moves a live call at once; outside a call it is kept for the next one.

import { Mic, Square } from 'lucide-react'
import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createTapAnalyser, readRms } from '../audio/useAmplitude'
import { useAudioDevices, type AudioDevice } from '../audio/useAudioDevices'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { SelectField } from '../ui/Field'
import { CallStatusContext } from './context'
import styles from './AudioDevices.module.css'

/** The choices for a device list. Until the microphone is allowed every device
    reports an empty id, which can't be chosen; and the browser's own "default"
    (and on Windows "communications") entries repeat a real device. All are left out. */
function options(devices: AudioDevice[], unnamed: string) {
  const choosable = devices.filter(
    (device, index) =>
      device.deviceId !== '' &&
      device.deviceId !== 'default' &&
      device.deviceId !== 'communications' &&
      devices.findIndex((other) => other.deviceId === device.deviceId) === index,
  )
  return [{ value: '', label: 'System default' }, ...choosable.map((device) => ({ value: device.deviceId, label: device.label || unnamed }))]
}

/** `active` is false while the panel holding this is closed but still mounted (a
    sheet keeps its contents for its exit animation): a running test stops. */
export function AudioDevices({ active = true }: { active?: boolean }) {
  const devices = useAudioDevices()
  // Read without the hook that insists on a provider: the design page shows this
  // with no call at all.
  const call = useContext(CallStatusContext)
  if (!devices.supported) {
    return <Notice tone="attention">This window can't reach your microphone or speakers. Open Veronica in the desktop app to choose them.</Notice>
  }
  return (
    <div className={styles.devices}>
      {devices.missing ? (
        <Notice tone="attention" title="No microphone found">
          Plug one in and it appears here at once.
        </Notice>
      ) : devices.refused ? (
        <Notice tone="attention" title="The microphone is blocked">
          Allow it for Veronica in your system's privacy settings, then come back here.
        </Notice>
      ) : (
        devices.needsPermission && (
          <Notice
            title="Device names are hidden"
            action={
              <Button size="sm" onClick={() => void devices.requestPermission()}>
                Allow the microphone
              </Button>
            }
          >
            Your computer shows their names once Veronica may use the microphone.
          </Notice>
        )
      )}
      <SelectField
        label="Microphone"
        value={devices.inputId}
        options={options(devices.inputs, 'Unnamed microphone')}
        onChange={(event) => {
          devices.selectInput(event.target.value)
          void call?.switchInput(event.target.value)
        }}
      />
      <MicLevel deviceId={devices.inputId} active={active} />
      {devices.outputSupported ? (
        <SelectField
          label="Speakers"
          value={devices.outputId}
          options={options(devices.outputs, 'Unnamed speakers')}
          onChange={(event) => {
            devices.selectOutput(event.target.value)
            void call?.switchOutput(event.target.value)
          }}
        />
      ) : (
        <p className={styles.note}>Choosing speakers isn't available here, so she uses the system default.</p>
      )}
    </div>
  )
}

/** A live level for the chosen microphone. The bar is written from the audio loop,
    so testing re-renders nothing. */
function MicLevel({ deviceId, active }: { deviceId: string; active: boolean }) {
  const [testing, setTesting] = useState(false)
  const [problem, setProblem] = useState<'refused' | 'failed' | null>(null)
  const bar = useRef<HTMLSpanElement>(null)
  const session = useRef<{ context: AudioContext; stream: MediaStream; frame: number } | null>(null)
  // Bumped by every start and stop, so a permission prompt answered after the test
  // was stopped (or the page left) can't switch the microphone back on.
  const attempt = useRef(0)

  const stop = useCallback(() => {
    attempt.current += 1
    const current = session.current
    session.current = null
    if (current) {
      cancelAnimationFrame(current.frame)
      current.stream.getTracks().forEach((track) => track.stop())
      current.context.close().catch(() => undefined)
    }
    if (bar.current) bar.current.style.transform = 'scaleX(0)'
    setTesting(false)
  }, [])

  const start = useCallback(async () => {
    const mine = ++attempt.current
    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: deviceId ? { exact: deviceId } : undefined } })
      if (mine !== attempt.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      const context = new AudioContext()
      const analyser = createTapAnalyser(context, context.createMediaStreamSource(stream))
      const buffer = new Float32Array(1024)
      session.current = { context, stream, frame: 0 }
      setProblem(null)
      setTesting(true)
      const loop = () => {
        const current = session.current
        if (!current) return
        if (bar.current) bar.current.style.transform = `scaleX(${Math.min(1, readRms(analyser, buffer) * 4)})`
        current.frame = requestAnimationFrame(loop)
      }
      loop()
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop())
      if (mine !== attempt.current) return
      // Refused is something you can fix in settings; anything else (the device is
      // busy in another app, or went away) is said as that, not as a refusal.
      setProblem(error instanceof DOMException && error.name === 'NotAllowedError' ? 'refused' : 'failed')
    }
  }, [deviceId])

  // A different microphone, or leaving: the test stops.
  useEffect(() => stop, [deviceId, stop])
  useEffect(() => {
    if (!active) stop()
  }, [active, stop])

  return (
    <div className={styles.level}>
      <Button
        size="sm"
        variant="secondary"
        icon={testing ? <Square size={11} fill="currentColor" aria-hidden="true" /> : <Mic size={15} aria-hidden="true" />}
        onClick={() => (testing ? stop() : void start())}
      >
        {testing ? 'Stop the test' : 'Test the microphone'}
      </Button>
      <span className={styles.track} aria-hidden="true">
        <span ref={bar} className={styles.bar} />
      </span>
      {problem && (
        <p className={styles.refused} role="alert">
          {problem === 'refused'
            ? "The microphone was refused. Allow it for Veronica in your system's privacy settings."
            : "Couldn't open that microphone. Another app may be using it."}
        </p>
      )}
    </div>
  )
}
