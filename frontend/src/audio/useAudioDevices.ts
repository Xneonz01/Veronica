// OS audio device routing: enumerate mic-input + speaker-output devices, persist
// the chosen ones, react to plug/unplug, and handle the permission gate
// (enumerateDevices returns blank labels until mic access is granted).

import { useCallback, useEffect, useState } from 'react'

export interface AudioDevice {
  deviceId: string
  label: string
}

const IN_KEY = 'veronica.audio.input'
const OUT_KEY = 'veronica.audio.output'

function read(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // persistence is best-effort (private mode / restricted webview)
  }
}

// Read the stored selection without the hook — used by useRealtimeCall at start.
export const storedDevice = {
  input: () => read(IN_KEY),
  output: () => read(OUT_KEY),
}

// AudioContext.setSinkId (output routing) needs Chromium >= 110; degrade if absent.
export const outputRoutingSupported =
  typeof AudioContext !== 'undefined' && 'setSinkId' in AudioContext.prototype

// Missing outside a secure context (a page served over plain http from another
// machine): the list stays empty and the picker says why, instead of crashing.
function mediaDevices(): MediaDevices | undefined {
  return typeof navigator === 'undefined' ? undefined : navigator.mediaDevices
}

export function useAudioDevices() {
  const [inputs, setInputs] = useState<AudioDevice[]>([])
  const [outputs, setOutputs] = useState<AudioDevice[]>([])
  const [inputId, setInputId] = useState(() => read(IN_KEY))
  const [outputId, setOutputId] = useState(() => read(OUT_KEY))
  const [needsPermission, setNeedsPermission] = useState(false)
  const [missing, setMissing] = useState(false)
  const [refused, setRefused] = useState(false)

  const enumerate = useCallback(async () => {
    let devices: MediaDeviceInfo[]
    try {
      devices = (await mediaDevices()?.enumerateDevices()) ?? []
    } catch {
      return // the lists keep what they had; the next devicechange tries again
    }
    const pick = (kind: MediaDeviceKind) =>
      devices
        .filter((d) => d.kind === kind)
        .map((d) => ({ deviceId: d.deviceId, label: d.label }))
    const ins = pick('audioinput')
    const outs = pick('audiooutput')
    setInputs(ins)
    setOutputs(outs)
    // No microphone at all is a different problem from one whose name is hidden:
    // labels are blank until mic permission is granted at least once.
    setMissing(ins.length === 0)
    setNeedsPermission(ins.some((d) => d.label === ''))
    // The stored choice is a preference: while its device is unplugged the system
    // default is used (and shown), and when it comes back, so does the choice. A
    // call reads the same stored id, so the picker never names a different device.
    const present = (list: AudioDevice[], id: string) => (id && list.some((d) => d.deviceId === id) ? id : '')
    setInputId(present(ins, read(IN_KEY)))
    setOutputId(present(outs, read(OUT_KEY)))
  }, [])

  useEffect(() => {
    const devices = mediaDevices()
    if (!devices) return
    void enumerate()
    devices.addEventListener('devicechange', enumerate)
    return () => devices.removeEventListener('devicechange', enumerate)
  }, [enumerate])

  const requestPermission = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach((t) => t.stop())
      setRefused(false)
      await enumerate()
    } catch (err) {
      // Only a refusal is "blocked", with the way to fix it. A missing or busy
      // microphone shows as what it is once the lists are read again.
      const name = err instanceof DOMException ? err.name : ''
      if (name === 'NotAllowedError' || name === 'SecurityError') setRefused(true)
      else await enumerate()
    }
  }, [enumerate])

  const selectInput = useCallback((id: string) => {
    setInputId(id)
    write(IN_KEY, id)
  }, [])

  const selectOutput = useCallback((id: string) => {
    setOutputId(id)
    write(OUT_KEY, id)
  }, [])

  return {
    inputs,
    outputs,
    inputId,
    outputId,
    needsPermission,
    missing,
    refused,
    supported: mediaDevices() !== undefined,
    outputSupported: outputRoutingSupported,
    selectInput,
    selectOutput,
    requestPermission,
  }
}
