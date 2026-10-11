// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CallState } from '../../audio/useRealtimeCall'
import { CallDock } from '../CallDock'
import { elapsed } from '../callCopy'
import { LiveLines } from '../LiveLines'
import { CallStatusContext, type CallStatus } from '../context'
import { AIMS, ease, moodOf, ringPhase } from '../orbModel'

afterEach(cleanup)

describe('the orb', () => {
  it('carries each call state as one of its moods', () => {
    const states: CallState[] = ['idle', 'connecting', 'warming_up', 'listening', 'thinking', 'speaking', 'error']
    expect(states.map(moodOf)).toEqual(['rest', 'waking', 'waking', 'listening', 'thinking', 'speaking', 'fault'])
  })

  it('eases toward a new mood without overshooting it', () => {
    let aim = AIMS.rest
    const trail: number[] = []
    for (let frame = 0; frame < 60; frame += 1) {
      aim = ease(aim, AIMS.speaking, 0.08)
      trail.push(aim.light)
    }
    expect(trail.every((light, index) => index === 0 || light >= trail[index - 1])).toBe(true)
    expect(Math.max(...trail)).toBeLessThanOrEqual(AIMS.speaking.light)
    expect(trail[trail.length - 1]).toBeGreaterThan(0.99 * AIMS.speaking.light)
  })

  it('keeps its speaking rings evenly spaced and always inside one cycle', () => {
    const phases = [0, 1, 2].map((index) => ringPhase(12.3, index, 3))
    for (const phase of phases) expect(phase).toBeGreaterThanOrEqual(0)
    for (const phase of phases) expect(phase).toBeLessThan(1)
    const sorted = [...phases].sort()
    expect(sorted[1] - sorted[0]).toBeCloseTo(1 / 3)
  })
})

describe('the live lines', () => {
  it("show what her reply said she did and didn't under it, apart from her words", () => {
    render(<LiveLines finalText="Forget the gym." partial="" reply="I've forgotten that." caveat="My memory didn't actually change just now. Ask me again if you want it to." state="speaking" />)
    const note = screen.getByText("My memory didn't actually change just now. Ask me again if you want it to.")
    expect(note.textContent).not.toContain("I've forgotten that.")
    expect(screen.getByText("I've forgotten that.")).toBeTruthy()
  })
})

describe('the clock', () => {
  it('reads the time on a call the way a clock does', () => {
    expect(elapsed(0)).toBe('0:00')
    expect(elapsed(65_000)).toBe('1:05')
    expect(elapsed(3_723_000)).toBe('1:02:03')
  })
})

describe('the call dock', () => {
  function status(overrides: Partial<CallStatus> = {}): CallStatus {
    return {
      state: 'listening',
      inCall: true,
      muted: false,
      since: Date.now(),
      ripple: 0,
      startCall: vi.fn(async () => undefined),
      endCall: vi.fn(),
      toggleMute: vi.fn(),
      switchInput: vi.fn(),
      switchOutput: vi.fn(),
      ...overrides,
    }
  }

  function renderDock(value: CallStatus, at = '/chat') {
    return render(
      <MemoryRouter initialEntries={[at]}>
        <CallStatusContext.Provider value={value}>
          <CallDock />
        </CallStatusContext.Provider>
      </MemoryRouter>,
    )
  }

  it('shows the call you are on elsewhere, with mute and end', async () => {
    const value = status()
    renderDock(value)
    expect(screen.getByRole('region', { name: 'Call in progress' }).textContent).toContain('Listening')
    await userEvent.click(screen.getByRole('button', { name: 'Mute microphone' }))
    await userEvent.click(screen.getByRole('button', { name: 'End the call' }))
    expect(value.toggleMute).toHaveBeenCalledOnce()
    expect(value.endCall).toHaveBeenCalledOnce()
  })

  it('stays out of the way with no call, and on the call page itself', () => {
    renderDock(status({ inCall: false }))
    expect(screen.queryByRole('region', { name: 'Call in progress' })).toBeNull()
    cleanup()
    renderDock(status(), '/call')
    expect(screen.queryByRole('region', { name: 'Call in progress' })).toBeNull()
  })
})
