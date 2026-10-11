import { describe, expect, it } from 'vitest'
import type { ConversationSummary } from '../../chat/types'
import { groupByAge, inProject } from '../recents'

function conversation(id: number, updated: Date, projectId: number | null = null): ConversationSummary {
  return {
    id,
    title: `Conversation ${id}`,
    created_at: updated.toISOString(),
    updated_at: updated.toISOString(),
    message_count: 2,
    project_id: projectId,
  }
}

// Wednesday 24 September 2026, mid-afternoon, local time.
const now = new Date(2026, 8, 24, 15, 30)
const at = (day: number, hour = 12) => new Date(2026, 8, day, hour)

describe('groupByAge', () => {
  it('sorts conversations into days as a person counts them', () => {
    const groups = groupByAge(
      [
        conversation(1, at(24, 9)),
        conversation(2, at(24, 0)),
        conversation(3, at(23, 23)),
        conversation(4, at(20)),
        conversation(5, at(1)),
        conversation(6, new Date(2026, 5, 1)),
      ],
      now,
    )
    expect(groups.map((group) => [group.label, group.items.map((item) => item.id)])).toEqual([
      ['Today', [1, 2]],
      ['Yesterday', [3]],
      ['Previous 7 days', [4]],
      ['Previous 30 days', [5]],
      ['Older', [6]],
    ])
  })

  it('leaves out the empty groups and keeps the server order', () => {
    const groups = groupByAge([conversation(9, at(24, 14)), conversation(8, at(24, 10))], now)
    expect(groups).toHaveLength(1)
    expect(groups[0].items.map((item) => item.id)).toEqual([9, 8])
  })

  it('files an unreadable date under Older rather than losing the conversation', () => {
    const broken = { ...conversation(7, at(24)), updated_at: 'not a date' }
    expect(groupByAge([broken], now)).toEqual([{ label: 'Older', items: [broken] }])
  })
})

describe('inProject', () => {
  const items = [conversation(1, at(24), 3), conversation(2, at(24)), conversation(3, at(24), 4)]

  it('shows everything when no project is chosen', () => {
    expect(inProject(items, null).map((item) => item.id)).toEqual([1, 2, 3])
  })

  it("shows only a project's own conversations", () => {
    expect(inProject(items, 3).map((item) => item.id)).toEqual([1])
  })
})
