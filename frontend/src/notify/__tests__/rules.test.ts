import { describe, expect, it } from 'vitest'
import { shouldNotify, wantsToast } from '../rules'
import type { MemoryAction, MemoryEvent, Notification } from '../types'

function event(action: MemoryAction, source: string, origin = 'agent'): MemoryEvent {
  return { type: 'memory_updated', action, memoryId: 3, kind: 'fact', summary: 'User keeps bees', confidence: 1, source, undoable: false, origin }
}

function notification(action: MemoryAction, source: string): Notification {
  return { id: 'n1', action, summary: 'User keeps bees', source, kind: 'fact', memoryId: 3, undoable: false, confidence: 1, createdAt: 1, members: [] }
}

describe('which memory writes are told', () => {
  it('tells a forget whoever first learned the memory', () => {
    // A forget runs only when the user asks; one of a memory she had learned on her
    // own raised nothing, not even the receipt under the answer.
    expect(shouldNotify(event('removed', 'inferred'))).toBe(true)
    expect(wantsToast(notification('removed', 'inferred'), 'subtle')).toBe(true)
  })

  it('keeps the Memory page and quiet learning quiet', () => {
    expect(shouldNotify(event('removed', 'user', 'ui'))).toBe(false)
    expect(shouldNotify(event('added', 'inferred'))).toBe(false)
    expect(wantsToast(notification('added', 'inferred'), 'subtle')).toBe(false)
    expect(wantsToast(notification('removed', 'user'), 'silent')).toBe(false)
  })
})
