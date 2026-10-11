// Recents, grouped the way people remember them: by the local day each
// conversation was last active. Pure, so the boundaries can be tested at a fixed
// moment.

import type { ConversationSummary } from '../chat/types'

export type RecentGroup = { label: string; items: ConversationSummary[] }

/** What to call a conversation: its title, or words for one that has none yet. */
export function titleOf(conversation: Pick<ConversationSummary, 'title'>): string {
  return conversation.title.trim() || 'Untitled conversation'
}

const BUCKETS: { label: string; daysBack: number }[] = [
  { label: 'Today', daysBack: 0 },
  { label: 'Yesterday', daysBack: 1 },
  { label: 'Previous 7 days', daysBack: 7 },
  { label: 'Previous 30 days', daysBack: 30 },
]

/** Keeps the incoming order (the server sends newest first); empty groups are left out. */
export function groupByAge(items: readonly ConversationSummary[], now: Date): RecentGroup[] {
  // Each boundary is a real local midnight, built from the calendar rather than by
  // subtracting 24 h, so a daylight-saving change can't move a conversation a day.
  const starts = BUCKETS.map(
    ({ daysBack }) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysBack).getTime(),
  )
  const groups: RecentGroup[] = [...BUCKETS.map(({ label }) => ({ label, items: [] })), { label: 'Older', items: [] }]
  for (const item of items) {
    const at = Date.parse(item.updated_at)
    const index = Number.isNaN(at) ? -1 : starts.findIndex((start) => at >= start)
    groups[index === -1 ? groups.length - 1 : index].items.push(item)
  }
  return groups.filter((group) => group.items.length > 0)
}

/** The conversations in a project, or all of them when none is chosen. */
export function inProject(
  items: readonly ConversationSummary[],
  projectId: number | null,
): ConversationSummary[] {
  return projectId === null ? [...items] : items.filter((item) => item.project_id === projectId)
}
