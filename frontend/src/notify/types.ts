// Types for the notification system. A MemoryEvent is a parsed memory frame from the
// event lane (/ws/events); a Notification is a queued, dismissable toast.

export type MemoryAction = 'added' | 'updated' | 'superseded' | 'candidate' | 'removed'

export interface MemoryEvent {
  type: 'memory_updated' | 'memory_learned'
  action: MemoryAction
  memoryId: number
  kind: string
  summary: string
  confidence: number
  source: string // "user" | "inferred" | "system"
  undoable: boolean
  origin: string // "agent" | "ui"
  active?: boolean // memory_learned: stored active vs queued candidate
  coalesceId?: string
  ts?: string
}

export interface Notification {
  id: string
  action: MemoryAction
  summary: string
  source: string // explicit = "user"
  kind: string
  memoryId: number
  undoable: boolean
  confidence: number
  createdAt: number
  coalesceId?: string
  ts?: string // backend ISO timestamp (for the awareness log's relative time)
  members: Notification[] // populated when this is a coalesced burst
  toasted?: boolean // shown as a toast, and so receipted under the answer it happened in
}

function num(value: unknown, fallback: number): number {
  return typeof value === 'number' ? value : fallback
}

export function toMemoryEvent(raw: Record<string, unknown>): MemoryEvent {
  return {
    type: raw.type as MemoryEvent['type'],
    action: (raw.action as MemoryAction) ?? 'updated',
    memoryId: num(raw.memory_id, num(raw.id, 0)),
    kind: String(raw.kind ?? ''),
    summary: String(raw.summary ?? raw.content ?? ''),
    confidence: num(raw.confidence, 1),
    source: String(raw.source ?? 'system'),
    undoable: raw.undoable === true,
    origin: String(raw.origin ?? 'agent'),
    active: raw.active as boolean | undefined,
    coalesceId: raw.coalesce_id as string | undefined,
    ts: raw.ts as string | undefined,
  }
}
