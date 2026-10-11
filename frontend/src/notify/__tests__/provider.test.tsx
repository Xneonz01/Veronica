// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { useContext } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NotificationContext } from '../context'
import { NotificationProvider } from '../NotificationProvider'
import type { MemoryEvent } from '../types'

const lane = vi.hoisted(() => ({ onMemoryUpdated: undefined as ((event: MemoryEvent) => void) | undefined }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: { onMemoryUpdated?: (event: MemoryEvent) => void }) => {
    lane.onMemoryUpdated = handlers.onMemoryUpdated
  },
}))
vi.mock('../../audio/notificationSounds', () => ({ playAction: vi.fn(), setSoundsEnabled: vi.fn() }))

function forgot(memoryId: number, summary: string): MemoryEvent {
  return { type: 'memory_updated', action: 'removed', memoryId, kind: 'fact', summary, confidence: 1, source: 'inferred', undoable: false, origin: 'agent' }
}

function History() {
  const context = useContext(NotificationContext)
  return (
    <ul>
      {context?.history.map((item) => (
        <li key={item.id}>{`${item.summary}: ${item.toasted ? 'toasted' : 'quiet'}`}</li>
      ))}
    </ul>
  )
}

afterEach(cleanup)

describe('the notification history', () => {
  it('keeps every toasted write, however many land in one render', () => {
    render(
      <NotificationProvider>
        <History />
      </NotificationProvider>,
    )

    act(() => {
      lane.onMemoryUpdated?.(forgot(1, 'User collects stamps'))
      lane.onMemoryUpdated?.(forgot(2, 'User collects coins'))
    })

    expect(screen.getByText('User collects stamps: toasted')).toBeTruthy()
    expect(screen.getByText('User collects coins: toasted')).toBeTruthy()
  })
})
