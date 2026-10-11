// Which memory writes raise a notification, and which of those are loud enough to toast.
// Pure, so the rules can be tested without the event lane.

import type { Verbosity } from './settings'
import type { MemoryAction, MemoryEvent, Notification } from './types'

/** What she did to her memory on the user's word: an explicit save, a correction, or a
 *  forget, which only runs when they ask, whoever first learned the memory. */
function onTheirWord(item: { source: string; action: MemoryAction }): boolean {
  return item.source === 'user' || item.action === 'superseded' || item.action === 'removed'
}

export function shouldNotify(event: MemoryEvent): boolean {
  if (event.type === 'memory_learned') return true
  // memory_updated: only the assistant's own writes on the user's word. The user's
  // Memory-page edits (origin "ui") and inferred duplicates are suppressed.
  return event.origin === 'agent' && onTheirWord(event)
}

// subtle (default) toasts only the loud moments (what she did on the user's word);
// auto-learned facts stay quiet (soft tick + nav pulse only).
export function wantsToast(notification: Notification, verbosity: Verbosity): boolean {
  if (verbosity === 'silent') return false
  if (verbosity === 'loud') return true
  return onTheirWord(notification)
}
