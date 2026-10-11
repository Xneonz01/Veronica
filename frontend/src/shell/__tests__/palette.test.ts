import { describe, expect, it } from 'vitest'
import { narrow, type PaletteGroup, type PaletteItem } from '../palette'

function item(group: PaletteGroup, label: string, keywords?: string[]): PaletteItem {
  return { id: `${group}:${label}`, group, label, icon: null, keywords, run: () => undefined }
}

const ITEMS: PaletteItem[] = [
  item('Conversations', 'Roof repairs'),
  item('Go to', 'Memory'),
  item('Go to', 'Settings', ['preferences', 'personality']),
  item('Actions', 'Collapse sidebar'),
  item('Projects', 'House'),
  item('Conversations', 'Birthday gift for Morgan'),
  item('Go to', 'Voices'),
]

const labels = (items: PaletteItem[]) => items.map((entry) => entry.label)

describe('narrow', () => {
  it('shows everything in group order when nothing is typed', () => {
    expect(labels(narrow(ITEMS, ''))).toEqual([
      'Memory',
      'Settings',
      'Voices',
      'Collapse sidebar',
      'House',
      'Roof repairs',
      'Birthday gift for Morgan',
    ])
  })

  it('needs every typed word, in the label or its keywords', () => {
    expect(labels(narrow(ITEMS, 'gift morgan'))).toEqual(['Birthday gift for Morgan'])
    expect(labels(narrow(ITEMS, 'personality'))).toEqual(['Settings'])
    expect(narrow(ITEMS, 'nothing like this')).toEqual([])
  })

  it('puts a label that starts with the query first', () => {
    const ranked = narrow(
      [item('Conversations', 'Notes on the memory card'), item('Conversations', 'Memory of the lake')],
      'mem',
    )
    expect(labels(ranked)).toEqual(['Memory of the lake', 'Notes on the memory card'])
  })

  it('keeps the Ask item while something is typed, whatever it is', () => {
    const ask = item('Ask', 'Ask Veronica')
    expect(labels(narrow([ask, ...ITEMS], 'zzz'))).toEqual(['Ask Veronica'])
  })

  it('keeps search-only items out of the resting list', () => {
    const dark = { ...item('Actions', 'Switch to the dark theme', ['appearance']), searchOnly: true }
    expect(labels(narrow([dark, ...ITEMS], ''))).not.toContain('Switch to the dark theme')
    expect(labels(narrow([dark, ...ITEMS], 'dark'))).toEqual(['Switch to the dark theme'])
    expect(labels(narrow([dark, ...ITEMS], 'appearance'))).toEqual(['Switch to the dark theme'])
  })

  it('lists a few recent conversations when idle and more when searching', () => {
    const many = Array.from({ length: 12 }, (_, index) => item('Conversations', `Chat about tea ${index}`))
    expect(narrow(many, '')).toHaveLength(5)
    expect(narrow(many, 'tea')).toHaveLength(8)
  })
})
