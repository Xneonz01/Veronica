// Call errors and their user-facing copy. Backend error `kind`s and local failures
// map to severity-tagged messages; raw server text is never shown.

import { MODEL_NAME } from '../chat/types'
import { memorySize } from '../shell/memorySize'
import { INSTALL } from '../shell/problemCopy'

export type ErrorSeverity = 'recoverable' | 'fatal'

export interface CallError {
  kind: string
  severity: ErrorSeverity
  message: string
}

// Backend error kinds -> copy.
const WIRE_ERROR_COPY: Record<string, { severity: ErrorSeverity; message: string }> = {
  stt_failed: {
    severity: 'recoverable',
    message: "I didn't catch that. Say it again when you're ready.",
  },
  llm_unreachable: {
    severity: 'recoverable',
    message: "I can't think right now: my language model isn't answering. I'm still listening; try again in a moment.",
  },
  reply_failed: {
    severity: 'recoverable',
    message: "Something tripped me up while I was answering. I'm still listening; try again.",
  },
  warmup_failed: {
    severity: 'fatal',
    message: "I couldn't start my voice. Check that the backend is running, then call again.",
  },
  card_tight: {
    severity: 'recoverable',
    message: "The graphics card is nearly full, so I may be slow to answer. Closing a program that's using it, like a game, would help.",
  },
  audio_transport_failed: {
    severity: 'fatal',
    message: "My voice stopped reaching you, so the call ended. Call again when you're ready.",
  },
}

// A kind this build doesn't know. If the server couldn't go on it closes the call,
// and the drop is reported as that; otherwise the call is still up.
const UNKNOWN_WIRE_ERROR = {
  severity: 'recoverable' as const,
  message: "Something went wrong on my side. I'm still listening; try again.",
}

/** The values an error event can carry, as they arrive: each is checked where it's used. */
export interface WireDetail {
  model?: unknown
  models?: unknown
  free_mib?: unknown
  total_mib?: unknown
  needs_mib?: unknown
}

export function wireError(kind: string | undefined, detail: WireDetail = {}): CallError {
  const model = typeof detail.model === 'string' ? detail.model : undefined
  if (kind === 'llm_model_missing') return modelMissing(model)
  if (kind === 'llm_no_tools') return noTools(model)
  if (kind === 'llm_cant_run') return cantRun(model)
  if (kind === 'models_missing') return modelsMissing(typeof detail.models === 'string' ? detail.models : undefined)
  if (kind === 'card_full') return cardFull(detail.free_mib, detail.needs_mib)
  if (kind === 'card_small') return cardSmall(detail.total_mib, detail.needs_mib)
  return { kind: kind ?? 'error', ...(WIRE_ERROR_COPY[kind ?? ''] ?? UNKNOWN_WIRE_ERROR) }
}

// Fatal: the call loaded nothing (bug 46: it started, and stopped answering). The backend
// refuses only with less free than the call needs; what is free is rounded down and the
// need up, so the two figures never read the same.
function cardFull(freeMib: unknown, needsMib: unknown): CallError {
  const free = memorySize(wholeMib(freeMib), 'down')
  const needs = memorySize(wholeMib(needsMib), 'up')
  const figures = free && needs ? ` A call needs ${needs} of it free, and ${free} is.` : ''
  return {
    kind: 'card_full',
    severity: 'fatal',
    message: `I can't start the call: the graphics card is nearly full.${figures} Close a program that's using it, like a game, then call again.`,
  }
}

// Fatal, and no program closed would help: a call needs more than the card has, with
// nothing else on it. What it has is rounded down and the need up, as above.
function cardSmall(totalMib: unknown, needsMib: unknown): CallError {
  const total = memorySize(wholeMib(totalMib), 'down')
  const needs = memorySize(wholeMib(needsMib), 'up')
  const figures = total && needs ? ` A call needs ${needs} of graphics memory, and it has ${total}.` : ''
  return {
    kind: 'card_small',
    severity: 'fatal',
    message: `I can't start the call: this graphics card is too small for one.${figures} A smaller language model may fit: set llm.model in my configuration to one, then restart my backend.`,
  }
}

// MiB as the backend sends them, digits in a string; anything else isn't a size.
function wholeMib(value: unknown): number | undefined {
  return typeof value === 'string' && /^\d{1,9}$/.test(value) ? Number(value) : undefined
}

// The call stays up: once the model is pulled, the next thing said is answered.
function modelMissing(model: string | undefined): CallError {
  const named = model && MODEL_NAME.test(model) ? model : null
  return {
    kind: 'llm_model_missing',
    severity: 'recoverable',
    message: named
      ? `I can't think right now: my language model, ${named}, isn't installed. Install it with "ollama pull ${named}", then try again.`
      : "I can't think right now: the language model my configuration names (llm.model) isn't installed. Install it with ollama pull and that name, then try again.",
  }
}

// Fatal: no turn of this call can be answered until another model is set and her backend
// restarted (bug 45: every turn failed as a passing hiccup).
function noTools(model: string | undefined): CallError {
  const named = model && MODEL_NAME.test(model) ? `my language model, ${model},` : 'my language model'
  return {
    kind: 'llm_no_tools',
    severity: 'fatal',
    message: `I can't answer: ${named} can't use tools, which I need. Set llm.model in my configuration to a model that can, then restart my backend and call again.`,
  }
}

// The call stays up: once the memory another program holds is free, the next thing said
// is answered.
function cantRun(model: string | undefined): CallError {
  const named = model && MODEL_NAME.test(model) ? model : 'my language model'
  return {
    kind: 'llm_cant_run',
    severity: 'recoverable',
    message: `I can't think right now: Ollama couldn't start ${named}, usually because the graphics card or memory is full. Close any program using them, then try again.`,
  }
}

// The models a call can't start without (the backend's `_CALL_NEEDS`), as she says them.
// A Map, so no code can reach an object's own properties (`constructor`).
const CALL_MODELS = new Map([
  ['stt', 'speech recognition'],
  ['tts', 'speech synthesis'],
  ['embedding', 'my memory model'],
])

// Fatal: nothing starts until they are installed and the backend restarted.
function modelsMissing(codes: string | undefined): CallError {
  const listed = String(codes ?? '').split(',').filter(Boolean)
  const named = listed.flatMap((code) => CALL_MODELS.get(code) ?? [])
  // Named only when every one has words here: one left out would read as all of them.
  const all = named.length > 0 && named.length === listed.length
  const one = all && named.length === 1
  const which = all ? joined(named) : 'some of my models'
  return {
    kind: 'models_missing',
    severity: 'fatal',
    message:
      `I can't start the call: ${which} ${one ? "isn't" : "aren't"} installed. ` +
      `Install ${one ? 'it' : 'them'} with ${INSTALL} in the backend folder, then restart the backend and call again.`,
  }
}

function joined(words: string[]): string {
  return words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

export const CONNECT_ERROR: CallError = {
  kind: 'connect_failed',
  severity: 'fatal',
  message: "Couldn't reach Veronica's voice service. Check that the backend is running, then call again.",
}

export const CALL_DROPPED: CallError = {
  kind: 'call_dropped',
  severity: 'fatal',
  message: 'The call dropped. Check that the backend is still running, then call again.',
}

export const MIC_ERROR: CallError = {
  kind: 'mic_denied',
  severity: 'fatal',
  message: "The microphone is blocked. Allow it for Veronica in your system's privacy settings, then call again.",
}

export const MIC_MISSING: CallError = {
  kind: 'mic_missing',
  severity: 'fatal',
  message: "I can't find a microphone. Plug one in, then call again.",
}

export const MIC_BUSY: CallError = {
  kind: 'mic_busy',
  severity: 'fatal',
  message: "Your microphone didn't start. Another app may be using it; close that app, then call again.",
}

/** The copy for a microphone that wouldn't open when a call started. */
export function micStartError(err: unknown): CallError {
  const name = err instanceof DOMException ? err.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return MIC_ERROR
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return MIC_MISSING
  return MIC_BUSY
}

export const MIC_LOST: CallError = {
  kind: 'mic_lost',
  severity: 'recoverable',
  message: 'Your microphone disconnected, so I switched to the default one.',
}

export const MIC_GONE: CallError = {
  kind: 'mic_gone',
  severity: 'recoverable',
  message: "Your microphone disconnected and there's no other one to use. Plug one in, or pick another in the call settings.",
}

export const MIC_SWITCH_FAILED: CallError = {
  kind: 'mic_switch_failed',
  severity: 'recoverable',
  message: "Couldn't switch to that microphone; another app may be using it. Still using the previous one.",
}

/** A problem a working microphone puts right. */
export function isMicProblem(error: CallError): boolean {
  return error.kind === MIC_LOST.kind || error.kind === MIC_GONE.kind || error.kind === MIC_SWITCH_FAILED.kind
}
