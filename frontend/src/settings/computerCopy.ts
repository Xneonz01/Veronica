// The words for each check of this computer, by the code the backend reports
// (`backend/app/doctor.py`). It sends a verdict and the few values the words need: sizes,
// a card's name, a model's name. Names are shown only in the shape a driver or Ollama
// gives them, and a size that isn't one leaves the plain line.

import { MODEL_NAME } from '../chat/types'
import { memorySize as size } from '../shell/memorySize'
import { problemCopy } from '../shell/problemCopy'
import type { ComputerCheck, Verdict } from '../system/useDoctor'

/** The checks in the order the backend runs them, and the label of each. */
export const CHECKS = new Map([
  ['gpu', 'Graphics card'],
  ['ram', 'Memory (RAM)'],
  ['disk', 'Disk space'],
  ['llm', 'Language model'],
  ['models', 'Speech and memory models'],
])

export type CheckWords = { body: string; status: string }
type Detail = ComputerCheck['detail']

const CARD_NAME = /^[\w .()+/-]{1,80}$/

const UNREADABLE = new Map([
  ['gpu', "Veronica couldn't read this graphics card's details."],
  ['ram', "Veronica couldn't read how much memory this computer has."],
  ['disk', "Veronica couldn't read the free space where her memory is kept."],
  ['llm', "Veronica couldn't check Ollama."],
  ['models', "Veronica couldn't check which of her models are installed."],
])
const STATUS: Record<Verdict, string> = { ok: 'Ready', warn: 'Limited', fail: 'Not ready', unknown: 'Unknown' }

export function checkWords({ check, code, verdict, detail }: ComputerCheck): CheckWords {
  const unreadable = code === 'unreadable' ? UNREADABLE.get(check) : undefined
  if (unreadable !== undefined) return { body: unreadable, status: 'Unknown' }
  const words = WORDS.get(check)?.(code, detail, verdict)
  // A code from a newer backend than this app, or values that aren't what it sends.
  if (!words) return { body: "This version of the app can't describe this result.", status: STATUS[verdict] }
  // A result that isn't fine never reads as ready, whatever its code says.
  return words.status === STATUS.ok && verdict !== 'ok' ? { ...words, status: STATUS[verdict] } : words
}

/** For a screen reader, after a check it asked for: only what has a row counts. */
export function summary(checks: ComputerCheck[]): string {
  const flagged = checks.filter((item) => CHECKS.has(item.check) && item.verdict !== 'ok').length
  if (flagged === 0) return 'Checked. Everything is ready.'
  return `Checked. ${flagged === 1 ? '1 thing needs' : `${flagged} things need`} attention.`
}

type Words = (code: string, detail: Detail, verdict: Verdict) => CheckWords | null

const WORDS = new Map<string, Words>([
  ['gpu', gpu],
  ['ram', ram],
  ['disk', disk],
  ['llm', llm],
  ['models', models],
])

function gpu(code: string, detail: Detail): CheckWords | null {
  if (code === 'absent') {
    return {
      body: 'No NVIDIA graphics card was found. By default her speech and memory models run on one, so chat and calls need it.',
      status: 'Not found',
    }
  }
  const short = code === 'small'
  // Short of what's needed, what it has is rounded down and the need up, so the two
  // figures never read the same.
  const total = size(detail.total_mib, short ? 'down' : 'nearest')
  const needs = size(detail.needs_mib, short ? 'up' : 'nearest')
  if (total === null || needs === null) return null
  const card = typeof detail.name === 'string' && CARD_NAME.test(detail.name) ? `${detail.name}, ${total}` : `${total} of graphics memory`
  if (code === 'ok') return { body: `${card}. Measured calls used up to ${needs}.`, status: 'Ready' }
  if (short) {
    return { body: `${card}: less than the ${needs} measured calls used. Calls may be slow, or fail to start.`, status: 'Limited' }
  }
  return null
}

function ram(code: string, detail: Detail): CheckWords | null {
  const short = code === 'small'
  const total = size(detail.total_mib, short ? 'down' : 'nearest')
  const needs = size(detail.needs_mib, short ? 'up' : 'nearest')
  if (total === null || needs === null) return null
  if (code === 'ok') {
    // Windows and every other program need memory too, and the measured figure leaves
    // them out: what's left for them is said rather than judged.
    const left = size(Number(detail.total_mib) - Number(detail.needs_mib), 'down')
    if (left === null) return null
    return {
      body: `${total}. In measured calls, her backend and Ollama used up to ${needs} together, which leaves ${left} for Windows and your other programs.`,
      status: 'Ready',
    }
  }
  if (short) {
    return { body: `${total}: less than the ${needs} her backend and Ollama used together in measured calls. She may be slow.`, status: 'Limited' }
  }
  return null
}

function disk(code: string, detail: Detail): CheckWords | null {
  if (code === 'full') {
    return {
      body: 'The disk where her memory is kept is full: less than 1 MB is free. Free up some space; until then she may not be able to save anything.',
      status: 'Full',
    }
  }
  const short = code === 'low'
  const free = size(detail.free_mib, short ? 'down' : 'nearest')
  const needs = size(detail.needs_mib, 'up')
  if (free === null || needs === null) return null
  if (code === 'ok') return { body: `${free} free where her memory is kept.`, status: 'Ready' }
  if (short) {
    return {
      body: `Only ${free} free where her memory is kept, and an update needs ${needs} there to keep a copy of her memory first. Free up some space before updating Veronica.`,
      status: 'Low',
    }
  }
  return null
}

function llm(code: string, detail: Detail): CheckWords | null {
  const model = typeof detail.model === 'string' && MODEL_NAME.test(detail.model) ? detail.model : null
  switch (code) {
    case 'ok':
      return { body: `Ollama is running and has ${model ?? 'her model'}.`, status: 'Ready' }
    case 'unreachable':
      return {
        body: "Ollama isn't answering, and chat and calls need it. Start Ollama, or check the address in her configuration (llm.base_url), then check again.",
        status: 'Not answering',
      }
    case 'model_missing':
      return {
        body: model
          ? `Ollama doesn't have ${model}. Install it with "ollama pull ${model}", then check again.`
          : "Ollama doesn't have the model her configuration names (llm.model). Install it with ollama pull and that name, then check again.",
        status: 'Not installed',
      }
    case 'no_tools':
      return {
        body: `Ollama says ${model ?? 'the model her configuration names'} can't use tools, which chat and calls rely on. Set llm.model in her configuration to one that can.`,
        status: 'No tools',
      }
    default:
      return null
  }
}

function models(code: string, detail: Detail, verdict: Verdict): CheckWords | null {
  if (code === 'ok') {
    const count = typeof detail.count === 'number' && Number.isInteger(detail.count) ? detail.count : null
    return { body: count === null ? 'All of them are installed.' : `All ${count} are installed.`, status: 'Ready' }
  }
  if (code === 'missing') {
    // The same words as the notice a start that found them missing shows.
    const { body } = problemCopy({ code: 'models_missing', detail: { models: detail.models ?? '' } })
    return { body, status: verdict === 'warn' ? 'Incomplete' : 'Not installed' }
  }
  return null
}
