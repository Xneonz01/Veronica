import { describe, expect, it } from 'vitest'
import { certainty, KIND, origin, slotLabel } from '../labels'

describe('memory labels', () => {
  it('names each kind of memory in words', () => {
    expect(Object.values(KIND)).toEqual(['About you', 'Fact', 'Preference', 'Session note', 'Call recap'])
  })

  it('names the slots she keeps, and spells out one she has made up', () => {
    expect(slotLabel('name')).toBe('Your name')
    expect(slotLabel('address_by_name')).toBe('How often she uses your name')
    expect(slotLabel('home_town')).toBe('Home town')
    expect(slotLabel(null)).toBe('About you')
  })

  it('says where a memory came from without the stored word for it', () => {
    const now = new Date().toISOString()
    expect(origin({ source: 'user', created_at: now })).toBe('You told her · just now')
    expect(origin({ source: 'inferred', created_at: now })).toBe('She picked it up · just now')
    expect(origin({ source: 'system', created_at: '' })).toBe('Noted')
  })

  it('puts confidence in words, clamped', () => {
    expect(certainty(0.724)).toBe('72% sure')
    expect(certainty(1.4)).toBe('100% sure')
  })
})
