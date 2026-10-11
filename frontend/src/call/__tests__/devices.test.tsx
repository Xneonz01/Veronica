// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioDevices } from '../AudioDevices'
import { CallStatusContext, type CallStatus } from '../context'

type Device = { deviceId: string; label: string; kind: MediaDeviceKind }

class FakeMediaDevices extends EventTarget {
  devices: Device[] = []
  enumerateDevices = vi.fn(async () => this.devices.map((device) => ({ ...device, groupId: '', toJSON: () => device })))
  getUserMedia = vi.fn(async (): Promise<MediaStream> => {
    throw new DOMException('Permission denied', 'NotAllowedError')
  })
}

let media: FakeMediaDevices

function install(devices: Device[]) {
  media = new FakeMediaDevices()
  media.devices = devices
  Object.defineProperty(navigator, 'mediaDevices', { value: media, configurable: true })
}

const mic = (deviceId: string, label: string): Device => ({ deviceId, label, kind: 'audioinput' })

function callStatus(): CallStatus {
  return {
    state: 'listening',
    inCall: true,
    muted: false,
    since: Date.now(),
    ripple: 0,
    startCall: vi.fn(async () => undefined),
    endCall: vi.fn(),
    toggleMute: vi.fn(),
    switchInput: vi.fn(async () => undefined),
    switchOutput: vi.fn(async () => undefined),
  }
}

const microphone = () => screen.getByRole('combobox', { name: 'Microphone' })
const choices = () => [...(microphone() as HTMLSelectElement).options].map((option) => option.textContent)

beforeEach(() => localStorage.clear())
afterEach(() => {
  cleanup()
  Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
})

describe('choosing a microphone', () => {
  it('explains, instead of crashing, when the window has no access to audio devices', () => {
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
    render(<AudioDevices />)
    expect(screen.getByText(/can't reach your microphone or speakers/)).toBeTruthy()
  })

  it('says there is no microphone, rather than that its name is hidden', async () => {
    install([])
    render(<AudioDevices />)
    expect(await screen.findByText('No microphone found')).toBeTruthy()
    expect(screen.queryByText('Device names are hidden')).toBeNull()
  })

  it('asks to see device names, and says how to fix a refusal', async () => {
    install([mic('', '')])
    render(<AudioDevices />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow the microphone' }))
    expect(await screen.findByText('The microphone is blocked')).toBeTruthy()
  })

  it("lists each device once, without the browser's own default entry", async () => {
    install([mic('default', 'Default - Desk mic'), mic('desk', 'Desk mic'), mic('desk', 'Desk mic'), mic('usb', 'USB headset')])
    render(<AudioDevices />)
    await screen.findByText('USB headset')
    expect(choices()).toEqual(['System default', 'Desk mic', 'USB headset'])
  })

  it('moves a live call to the chosen microphone and remembers it', async () => {
    install([mic('desk', 'Desk mic'), mic('usb', 'USB headset')])
    const status = callStatus()
    render(
      <CallStatusContext.Provider value={status}>
        <AudioDevices />
      </CallStatusContext.Provider>,
    )
    await screen.findByText('USB headset')
    await userEvent.selectOptions(microphone(), 'usb')
    expect(status.switchInput).toHaveBeenCalledWith('usb')
    expect(localStorage.getItem('veronica.audio.input')).toBe('usb')
  })

  it('falls back to the system default when the chosen microphone is unplugged', async () => {
    localStorage.setItem('veronica.audio.input', 'usb')
    install([mic('desk', 'Desk mic'), mic('usb', 'USB headset')])
    render(<AudioDevices />)
    await screen.findByText('USB headset')
    expect((microphone() as HTMLSelectElement).value).toBe('usb')
    media.devices = [mic('desk', 'Desk mic')]
    await act(async () => {
      media.dispatchEvent(new Event('devicechange'))
    })
    expect((microphone() as HTMLSelectElement).value).toBe('')
    expect(choices()).toEqual(['System default', 'Desk mic'])
    // Plugged back in, it is the choice again: a call would use it, so the picker says so.
    media.devices = [mic('desk', 'Desk mic'), mic('usb', 'USB headset')]
    await act(async () => {
      media.dispatchEvent(new Event('devicechange'))
    })
    expect((microphone() as HTMLSelectElement).value).toBe('usb')
  })

  it('leaves out the copies Windows adds of a real device', async () => {
    install([mic('communications', 'Communications - Desk mic'), mic('desk', 'Desk mic')])
    render(<AudioDevices />)
    await screen.findByText('Desk mic')
    expect(choices()).toEqual(['System default', 'Desk mic'])
  })

  it('keeps the list it has when the devices cannot be read', async () => {
    install([mic('desk', 'Desk mic')])
    render(<AudioDevices />)
    await screen.findByText('Desk mic')
    media.enumerateDevices.mockRejectedValueOnce(new Error('busy'))
    await act(async () => {
      media.dispatchEvent(new Event('devicechange'))
    })
    expect(choices()).toEqual(['System default', 'Desk mic'])
  })
})
