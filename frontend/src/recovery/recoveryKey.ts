// A recovery key as a person types or pastes it back (backend `core/recovery_key.py`):
// 36 letters and digits, however it is spaced, split or cased. The backend reads the key
// and its check; this only says early what can't be one.

import { failureMessage } from '../lib/api'
import { isRecord } from '../shell/useProblems'

const LENGTH = 36

// The app's words for each refusal of the recovery key routes, by its code. A Map, so no
// code can reach an object's own properties.
const REFUSALS = new Map([
  ['recovery_key_invalid', "That isn't a recovery key. Check it for a typo: it has 36 letters and digits, in groups of four."],
  ['recovery_key_wrong', "That recovery key doesn't open her memory or voices. It may be one made before the newest."],
  ['recovery_key_none', 'No recovery key was made for her memory or voices, so none can open them.'],
  [
    'recovery_unsupported',
    "A recovery key needs Windows, which keeps her keys. On this system her data isn't encrypted, so there is nothing for one to open.",
  ],
  [
    'recovery_locked',
    "Her memory or voices are locked on this computer, so a recovery key can't be made until they open. The notice at the top of the window says what to do.",
  ],
  ['recovery_nothing', 'There is nothing encrypted yet for a recovery key to open.'],
])

/** What to say for a refused request: the app's words for its code, else the safe
    fallback the shared reader chooses. */
export async function refusalWords(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.clone().json()
    const words = isRecord(body) && typeof body.code === 'string' ? REFUSALS.get(body.code) : undefined
    if (words) return words
  } catch {
    // no JSON body: the shared reader says so
  }
  return failureMessage(response, fallback)
}

/** The letters and digits in `text`, in capitals: what the backend reads. */
export function compactKey(text: string): string {
  return text.toUpperCase().replace(/[^0-9A-Z]/g, '')
}

/** Why `text` can't be a recovery key, in words; null when it may be one. */
export function keyProblem(text: string): string | null {
  const compact = compactKey(text)
  if (compact.length === 0) return 'Enter the recovery key you saved.'
  if (compact.includes('U')) return 'A recovery key has no letter U. Check it for a typo.'
  if (compact.length !== LENGTH) {
    const count = `${compact.length} ${compact.length === 1 ? 'character' : 'characters'}`
    return `That's ${count}. A recovery key has ${LENGTH}, in groups of four.`
  }
  return null
}

/** The stores a recovery key reached, as her words name them: "her memory and voices". */
export function storesNamed(stores: readonly string[]): string {
  const named = stores.map((store) => (store === 'memory' ? 'memory' : 'voices'))
  return `her ${named.join(' and ')}`
}
