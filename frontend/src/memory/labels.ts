// How the Memory page names things: kinds of memory, the slots she keeps about
// you, where a memory came from, and how she learns. No stored identifier
// reaches the screen as it is.

import { relativeTime } from './format'
import type { LearningMode, MemoryKind, MemoryRow } from './types'

export const KIND: Record<MemoryKind, string> = {
  profile: 'About you',
  fact: 'Fact',
  preference: 'Preference',
  episodic: 'Session note',
  summary: 'Call recap',
}

/** A kind this page knows by name, or plain "Memory" for one added later. */
export function kindLabel(kind: string): string {
  return (KIND as Record<string, string | undefined>)[kind] ?? 'Memory'
}

// The identity slots she keeps. An unknown one is spelled out from its key.
const SLOTS: Record<string, string> = {
  name: 'Your name',
  nickname: 'What to call you',
  address_by_name: 'How often she uses your name',
}

export function slotLabel(key: string | null): string {
  if (!key) return KIND.profile
  const known = SLOTS[key]
  if (known) return known
  const words = key.replace(/[_.-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Where it came from and when: "You told her · 3d ago", "She picked it up · …". */
export function origin(memory: Pick<MemoryRow, 'source' | 'created_at'>): string {
  const how =
    memory.source === 'user'
      ? 'You told her'
      : memory.source === 'inferred'
        ? 'She picked it up'
        : memory.source === 'session'
          ? 'From a conversation'
          : 'Noted'
  const when = relativeTime(memory.created_at)
  return when ? `${how} · ${when}` : how
}

/** How sure she is of something she inferred, in words. */
export function certainty(confidence: number): string {
  const percent = Math.round(Math.min(1, Math.max(0, confidence)) * 100)
  return `${percent}% sure`
}

export const LEARNING: Record<LearningMode, { label: string; means: string }> = {
  proactive: {
    label: 'Automatic',
    means: "She keeps what she's confident about and asks you about the rest.",
  },
  review_only: {
    label: 'Ask me first',
    means: 'Everything she picks up waits here for you to keep or drop.',
  },
}
