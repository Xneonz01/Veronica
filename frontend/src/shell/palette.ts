// What the command palette offers and how it narrows as you type. Pure, so the
// ranking can be tested without a dialog.

import type { ReactNode } from 'react'

export const GROUPS = ['Ask', 'Go to', 'Actions', 'Projects', 'Conversations'] as const
export type PaletteGroup = (typeof GROUPS)[number]

export interface PaletteItem {
  id: string
  group: PaletteGroup
  label: string
  icon: ReactNode
  /** Right-aligned: a shortcut, a count, a date. */
  hint?: string
  /** Other words it should answer to ("preferences" for Settings). */
  keywords?: readonly string[]
  /** Offered only once something is typed, so the resting list stays short. */
  searchOnly?: boolean
  run: () => void
}

/** How many conversations to list: a few recents when idle, more when searching. */
const CONVERSATIONS = { idle: 5, searching: 8 }

/**
 * The items that match every word of the query, grouped in GROUPS order and, within
 * a group, best match first: the label starting with the query, then a word of it
 * starting with it, then anywhere. With no query every item stays, in given order.
 */
export function narrow(items: readonly PaletteItem[], query: string): PaletteItem[] {
  const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean)
  const scored = items
    .map((item, order) => ({ item, order, score: words.length === 0 ? 0 : score(item, words, query) }))
    .filter(({ item, score }) => score >= 0 && (words.length > 0 || !item.searchOnly))

  const out: PaletteItem[] = []
  for (const group of GROUPS) {
    const members = scored
      .filter(({ item }) => item.group === group)
      .sort((a, b) => b.score - a.score || a.order - b.order)
      .map(({ item }) => item)
    const limit = group === 'Conversations' ? CONVERSATIONS[words.length ? 'searching' : 'idle'] : Infinity
    out.push(...members.slice(0, limit))
  }
  return out
}

/** -1 when a word is missing; higher is a better match. */
function score(item: PaletteItem, words: string[], query: string): number {
  // The Ask item carries the query itself, so it always matches while one is typed.
  if (item.group === 'Ask') return 0
  const label = item.label.toLocaleLowerCase()
  const haystack = [label, ...(item.keywords ?? []).map((word) => word.toLocaleLowerCase())].join(' ')
  if (!words.every((word) => haystack.includes(word))) return -1
  const whole = query.trim().toLocaleLowerCase()
  if (label.startsWith(whole)) return 3
  if (label.split(/\s+/).some((word) => word.startsWith(words[0]))) return 2
  return 1
}
