// What she says when an answer goes wrong, by the kind of failure the chat socket
// reports. The backend's own message is never shown: this is the copy, and an
// unknown kind gets a plain line rather than whatever the server sent.

import type { ErrorDetail } from '../chat/types'

export type ChatProblem = {
  title: string
  body: string
  /** Asking the same question again could work. */
  retry: boolean
}

const PROBLEMS: Record<string, ChatProblem> = {
  llm_unreachable: {
    title: "I can't think right now",
    body: "My language model isn't answering. Check that Ollama is running, then ask again.",
    retry: true,
  },
  turn_failed: {
    title: 'Something interrupted that answer',
    body: 'Nothing else was affected. Ask again and I will start over.',
    retry: true,
  },
  busy: {
    title: "I'm still on the last one",
    body: 'Let that answer finish, or stop it, then ask again.',
    retry: false,
  },
  bad_conversation: {
    title: "That conversation isn't here any more",
    body: 'It may have been deleted in another window. Start a new chat to carry on.',
    retry: false,
  },
  // Sent back, not saved, and put back in the box: the same message again would be
  // sent back again. A document needs no conversation, so this works on a first message.
  // Not "send it in parts": the window holds about one such part, so the first would be
  // gone by the last.
  message_too_long: {
    title: "That's more than I can read at once",
    body: "It's back in the box. Add it on the Memory page under Documents with Paste text, then ask me about it: I'll find the parts that answer you.",
    retry: false,
  },
  // No message is short enough: a setting, which only the person running her can change.
  context_too_small: {
    title: 'My settings leave no room for a message',
    body: "It's back in the box. My personality and instructions fill my context window. Shorten the personality in Settings, or raise llm.num_ctx (or lower llm.num_predict) in my configuration and restart my backend.",
    retry: false,
  },
  connection_lost: {
    title: 'The connection dropped before I finished',
    body: "Anything I had already done is kept. Ask again once I'm back.",
    retry: true,
  },
  offline: {
    title: "I can't reach my backend",
    body: "Your message wasn't sent; it's still in the box. Send it again once I'm back.",
    retry: false,
  },
  // Said after a message that couldn't be sent and after a conversation that couldn't
  // be opened, so it promises nothing about either.
  memory_unavailable: {
    title: "I can't open my memory",
    body: "Conversations are kept there, so chat is paused until that's sorted out. The main window says what to do.",
    retry: false,
  },
}

const UNKNOWN: ChatProblem = { title: 'Something went wrong with that answer', body: 'Ask again in a moment.', retry: true }

export function chatProblem(kind: string | null, detail: ErrorDetail = {}): ChatProblem | null {
  if (kind === null) return null
  if (kind === 'llm_model_missing') return modelMissing(detail.model)
  if (kind === 'llm_no_tools') return noTools(detail.model)
  if (kind === 'llm_cant_run') return cantRun(detail.model)
  return PROBLEMS[kind] ?? UNKNOWN
}

// Another model is the fix, and only whoever runs her can choose one: asking again never
// helps, so it isn't offered (bug 45: every message failed as a passing hiccup).
function noTools(model: string | undefined): ChatProblem {
  const named = model ?? 'The model my configuration names (llm.model)'
  return {
    title: "My language model can't use tools",
    body: `${named} can't use tools, and I need them to remember things and work with your files. Set llm.model in my configuration to a model that can (Ollama's library marks them "tools"), then restart my backend.`,
    retry: false,
  }
}

// Often memory another program holds: once it's free, the same question goes through.
function cantRun(model: string | undefined): ChatProblem {
  return {
    title: "My language model couldn't start",
    body: `Ollama stopped ${model ?? 'it'} before it could answer, usually because the graphics card or memory is full. Close any program using them, then ask again.`,
    retry: true,
  }
}

// Pulling it is the fix, so asking again is offered (a button, where there is one: the
// words don't promise it, since the companion has none): it works once the model is there.
function modelMissing(model: string | undefined): ChatProblem {
  return {
    title: "My language model isn't installed",
    body: model
      ? `Ollama doesn't have ${model}. Install it with "ollama pull ${model}".`
      : "Ollama doesn't have the model my configuration names (llm.model). Install it with ollama pull and that name.",
    retry: true,
  }
}
