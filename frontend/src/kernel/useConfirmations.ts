// Pending capability confirmations: actions waiting on the user's approval.
//
// Kept out of the notification/toast queue on purpose. Toasts are disposable: they
// auto-dismiss, coalesce into bursts and are capped by MAX_VISIBLE. A confirmation
// blocks an action, and dropping one silently would deny it with nothing on screen
// to say why.

import { useCallback, useEffect, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { apiFetch } from '../lib/api'

/** Why she asks for an action its setting would have let run: an argument came word
 *  for word from something she read, the turn read the web, or a call to the web would
 *  send an address nobody gave her, a secret, or words of yours (`kernel/egress.py`). */
export type AskReason = 'from_reading' | 'after_web' | 'unfamiliar_link' | 'sends_secret' | 'sends_private'
const REASONS: readonly string[] = ['from_reading', 'after_web', 'unfamiliar_link', 'sends_secret', 'sends_private']

/** One argument as the prompt shows it: hidden characters revealed, a secret blanked. */
export interface ConfirmDetail {
  field: string
  value: string
  /** Characters cut from `value`; the whole of it comes from `fullValues`. */
  more: number
  /** Characters blanked in `value` as a secret; `fullValues` has them, so nothing can
   *  hide behind one from whoever answers. */
  masked: number
}

export interface Confirmation {
  token: string
  capability: string
  summary: string
  risk: string
  expiresInMs: number
  /** Absolute deadline, so a resync mid-window resumes at the right point. */
  expiresAt: number
  why: AskReason | null
  /** Where it lands: the host a page is read from, as the request resolves it, or the
   *  file it writes. */
  target: string
  /** That host as people read it, when it is an international name: `аpple.com` is
   *  `xn--pple-43d.com`, not Apple's. Empty otherwise. */
  targetReadAs: string
  details: ConfirmDetail[]
}

// The server's expiry event normally clears the card; this grace period keeps the
// local sweep from racing ahead of it.
const EXPIRY_GRACE_MS = 1500

interface WireConfirm {
  token?: unknown
  capability?: unknown
  summary?: unknown
  risk?: unknown
  expires_in_ms?: unknown
  why?: unknown
  display?: { target?: unknown; target_read_as?: unknown; details?: unknown } | null
}

function count(raw: unknown): number {
  return typeof raw === 'number' && raw > 0 ? raw : 0
}

function toDetails(raw: unknown): ConfirmDetail[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object') return []
    const { field, value, more, masked } = item as Record<string, unknown>
    if (typeof field !== 'string' || typeof value !== 'string') return []
    return [{ field, value, more: count(more), masked: count(masked) }]
  })
}

function toConfirmation(raw: WireConfirm): Confirmation | null {
  if (typeof raw.token !== 'string' || !raw.token) return null
  const expiresInMs = typeof raw.expires_in_ms === 'number' ? raw.expires_in_ms : 30000
  const display = raw.display ?? {}
  return {
    token: raw.token,
    capability: String(raw.capability ?? ''),
    summary: String(raw.summary ?? ''),
    risk: String(raw.risk ?? 'guarded'),
    expiresInMs,
    expiresAt: Date.now() + expiresInMs,
    why: typeof raw.why === 'string' && REASONS.includes(raw.why) ? (raw.why as AskReason) : null,
    target: typeof display.target === 'string' ? display.target : '',
    targetReadAs: typeof display.target_read_as === 'string' ? display.target_read_as : '',
    details: toDetails(display.details),
  }
}

export function useConfirmations() {
  const [pending, setPending] = useState<Confirmation[]>([])

  const drop = useCallback((token: string) => {
    setPending((current) => current.filter((item) => item.token !== token))
  }, [])

  // A surface that reloaded (or connected late) must re-render an outstanding
  // prompt; otherwise the request hangs invisibly until it times out.
  const resync = useCallback(async () => {
    const asked = Date.now()
    try {
      const response = await apiFetch('/capabilities/pending')
      if (!response.ok) return
      const body = (await response.json()) as { pending?: WireConfirm[] }
      const items = (body.pending ?? []).map(toConfirmation).filter((x): x is Confirmation => !!x)
      // The server's list replaces what was here, except a prompt that arrived
      // while it was being fetched: the list was made before it existed.
      setPending((current) => [
        ...items,
        ...current.filter(
          (item) => item.expiresAt - item.expiresInMs >= asked && !items.some((fresh) => fresh.token === item.token),
        ),
      ])
    } catch {
      // offline: the lane's reconnect will call us again
    }
  }, [])

  useAppEvents({
    onConnected: () => void resync(),
    onConfirmRequired: (raw) => {
      const confirmation = toConfirmation(raw)
      if (!confirmation) return
      // A resync racing the live event can deliver the same token twice. Appending
      // blindly would render two cards with duplicate React keys, and answering one
      // would leave the other stranded on screen.
      setPending((current) =>
        current.some((item) => item.token === confirmation.token)
          ? current
          : [...current, confirmation],
      )
    },
    onConfirmExpired: (token) => drop(token),
    // Answering by voice mid-call resolves the same token this card holds, so the card
    // has to close too.
    onConfirmResolved: (token) => drop(token),
  })

  useEffect(() => {
    void resync()
  }, [resync])

  // Drop cards whose deadline has passed. The backend publishes
  // `capability_confirm_expired`, but that publish can be interrupted on a cancelled
  // turn, which would leave a card offering Approve on a token the server has already
  // discarded. This is a client-side backstop, not the security boundary: the server
  // treats the request as denied once its window closes.
  useEffect(() => {
    if (pending.length === 0) return
    const sweep = window.setInterval(() => {
      const now = Date.now()
      setPending((current) => {
        const live = current.filter((item) => item.expiresAt + EXPIRY_GRACE_MS > now)
        return live.length === current.length ? current : live
      })
    }, 1000)
    return () => window.clearInterval(sweep)
  }, [pending.length])

  /** False when the answer didn't land. The backend fails closed on its own
   *  timeout, so a lost answer ends as a denial, never as a silent approval, but
   *  someone who said yes needs to hear that nothing was done. */
  const answer = useCallback(
    async (token: string, approved: boolean): Promise<boolean> => {
      drop(token) // optimistic: the prompt goes as soon as they choose
      try {
        const response = await apiFetch('/capabilities/confirm', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, approved }),
        })
        if (!response.ok) return false
        // A request that had already run out, or was answered by voice, comes back
        // as 200 with `resolved: false`: the answer changed nothing.
        const body = (await response.json()) as { resolved?: unknown }
        return body.resolved === true
      } catch {
        return false
      }
    },
    [drop],
  )

  /** Every argument of a request in full, screened as the prompt is. Fetched only for
   *  the prompt on screen, so a long file body isn't sent to every window; null when it
   *  can't be had (answered, run out, offline). */
  const fullValues = useCallback(async (token: string): Promise<Record<string, string> | null> => {
    try {
      const response = await apiFetch(`/capabilities/pending/${encodeURIComponent(token)}`)
      if (!response.ok) return null
      const body = (await response.json()) as { values?: unknown }
      if (!body.values || typeof body.values !== 'object') return null
      const values = Object.entries(body.values).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
      return Object.fromEntries(values)
    } catch {
      return null
    }
  }, [])

  return { pending, answer, fullValues }
}
