import { describe, expect, it } from 'vitest'
import { readErrorDetail, type ChatMessage } from '../../chat/types'
import { chatProblem } from '../chatProblems'
import { isJustSent, toTurns } from '../turns'

function message(id: number, role: 'user' | 'assistant', content = ''): ChatMessage {
  return { id, role, content, citations: [], tools: [] }
}

describe('toTurns', () => {
  it('pairs each question with the answer after it', () => {
    const turns = toTurns([message(1, 'user'), message(2, 'assistant'), message(3, 'user'), message(4, 'assistant')])
    expect(turns.map((turn) => [turn.question?.id, turn.answer?.id])).toEqual([
      [1, 2],
      [3, 4],
    ])
  })

  it('leaves the newest question waiting while she thinks', () => {
    const turns = toTurns([message(1, 'user'), message(2, 'assistant'), message(-1, 'user')])
    expect(turns[1]).toMatchObject({ question: { id: -1 }, answer: null })
    expect(isJustSent(turns[1])).toBe(true)
    expect(isJustSent(turns[0])).toBe(false)
  })

  it('gives an answer with no question its own turn', () => {
    const turns = toTurns([message(1, 'assistant'), message(2, 'user')])
    expect(turns.map((turn) => [turn.question?.id ?? null, turn.answer?.id ?? null])).toEqual([
      [null, 1],
      [2, null],
    ])
  })
})

describe('chatProblem', () => {
  it('has its own words for each failure and a plain line for the rest', () => {
    expect(chatProblem('llm_unreachable')).toMatchObject({ retry: true })
    expect(chatProblem('offline')?.body).toContain('still in the box')
    expect(chatProblem('something_new')).toEqual({
      title: 'Something went wrong with that answer',
      body: 'Ask again in a moment.',
      retry: true,
    })
    expect(chatProblem(null)).toBeNull()
  })

  it('turns a message too long to read towards a document, and offers no retry that would fail again', () => {
    // A document needs no conversation: attaching one did, and a first message has none.
    const tooLong = chatProblem('message_too_long')
    expect(tooLong?.title).toBe("That's more than I can read at once")
    expect(tooLong?.body).toContain("It's back in the box.")
    expect(tooLong?.body).toContain('Memory page under Documents with Paste text')
    expect(tooLong?.body).not.toContain('shorter parts') // the first part would be gone by the last
    expect(tooLong?.retry).toBe(false)
  })

  it('names the setting when no message could be short enough', () => {
    const tooSmall = chatProblem('context_too_small')
    expect(tooSmall?.body).toContain('llm.num_ctx')
    expect(tooSmall?.body).toContain('Shorten the personality in Settings')
    expect(tooSmall?.retry).toBe(false)
  })

  it('names a chat model that is not installed, and how to install it (bug 30)', () => {
    const named = chatProblem('llm_model_missing', { model: 'qwen3.5:9b' })
    expect(named?.body).toBe(`Ollama doesn't have qwen3.5:9b. Install it with "ollama pull qwen3.5:9b".`)
    expect(named?.retry).toBe(true) // asking again works once it is pulled
    expect(chatProblem('llm_model_missing')?.body).toContain('ollama pull and that name')
  })
})

describe('a refusal by Ollama that asking again cannot mend (bug 45)', () => {
  it('says a model without tools needs another model, and offers no retry', () => {
    const noTools = chatProblem('llm_no_tools', { model: 'smollm:135m' })
    expect(noTools?.title).toBe("My language model can't use tools")
    expect(noTools?.body).toContain("smollm:135m can't use tools")
    expect(noTools?.body).toContain('Set llm.model in my configuration to a model that can')
    expect(noTools?.retry).toBe(false)
    expect(chatProblem('llm_no_tools')?.body).toContain('The model my configuration names (llm.model)')
  })

  it('says what to free when the model could not start, and offers a retry', () => {
    const cantRun = chatProblem('llm_cant_run', { model: 'qwen3.5:9b' })
    expect(cantRun?.title).toBe("My language model couldn't start")
    expect(cantRun?.body).toContain('Ollama stopped qwen3.5:9b before it could answer')
    expect(cantRun?.body).toContain('Close any program using them, then ask again.')
    expect(cantRun?.retry).toBe(true)
  })
})

describe('readErrorDetail', () => {
  it('keeps a model name only in the shape Ollama gives one', () => {
    expect(readErrorDetail({ kind: 'llm_model_missing', model: 'hf.co/someone/model:Q4_K_M' })).toEqual({
      model: 'hf.co/someone/model:Q4_K_M',
    })
    for (const model of ['<img src=x>', 'a b', 7, '', 'x'.repeat(201)]) {
      expect(readErrorDetail({ model })).toEqual({})
    }
  })
})
