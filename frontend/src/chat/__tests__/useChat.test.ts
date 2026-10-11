// Tests for the chat streaming reducers, which run without a socket.
//
// The reducers decide what the transcript looks like mid-turn: which bubble a tool
// card lands in, and whether the final reply replaces the streamed preview or
// duplicates it.

import { describe, expect, it } from 'vitest'
import { appendToken, attachCitations, attachTool, interrupt, settleReply } from '../transcript'
import { foldStoredMessages, readCaveats, type ChatMessage, type WireMessage } from '../types'

const ids = () => ({ current: -1 })

function assistant(partial: Partial<ChatMessage> = {}): ChatMessage {
  return { id: 1, role: 'assistant', content: '', citations: [], tools: [], ...partial }
}

describe('appendToken', () => {
  it('opens one bubble and grows it', () => {
    const ref = ids()
    let state = appendToken([], 'Hel', ref)
    state = appendToken(state, 'lo', ref)
    expect(state).toHaveLength(1)
    expect(state[0]).toMatchObject({ role: 'assistant', content: 'Hello', streaming: true })
  })

  it('does not append into a settled reply', () => {
    // Otherwise a late token from a finished turn mutates the committed answer.
    const settled = assistant({ content: 'Done.', streaming: false })
    const state = appendToken([settled], ' extra', ids())
    expect(state).toHaveLength(2)
    expect(state[0].content).toBe('Done.')
  })

  it('starts a new bubble after a user turn', () => {
    const user: ChatMessage = { id: 1, role: 'user', content: 'hi', citations: [], tools: [] }
    const state = appendToken([user], 'Hello', ids())
    expect(state.map((m) => m.role)).toEqual(['user', 'assistant'])
  })
})

describe('attachTool', () => {
  it('opens a bubble when the tool fires before any prose', () => {
    // The model may call a tool before writing any prose; the card still needs a
    // bubble before the first token arrives.
    const state = attachTool([], { name: 'files_read', result: 'x', ok: true, code: '' }, ids())
    expect(state).toHaveLength(1)
    expect(state[0].tools).toHaveLength(1)
    expect(state[0].streaming).toBe(true)
  })

  it('adds to the bubble already streaming', () => {
    const ref = ids()
    let state = appendToken([], 'Looking…', ref)
    state = attachTool(state, { name: 'files_list', result: 'y', ok: true, code: '' }, ref)
    expect(state).toHaveLength(1)
    expect(state[0].tools.map((t) => t.name)).toEqual(['files_list'])
  })

  it('keeps failed runs, so a refusal is still visible', () => {
    const state = attachTool(
      [],
      { name: 'files_read', result: 'Outside the folders I can open.', ok: false, code: 'path_outside_roots' },
      ids(),
    )
    expect(state[0].tools[0].ok).toBe(false)
  })
})

describe('settleReply', () => {
  it('replaces the streamed preview rather than appending to it', () => {
    // The server strips invented [n] markers, so its text is authoritative; appending
    // would show the answer twice.
    const ref = ids()
    const streamed = appendToken([], 'draft with [7]', ref)
    const state = settleReply(streamed, 'draft', ref)
    expect(state).toHaveLength(1)
    expect(state[0]).toMatchObject({ content: 'draft', streaming: false })
  })

  it('keeps what the answer said she did and she did not, beside the text', () => {
    const ref = ids()
    const caveats = [{ family: 'memory', text: "My memory didn't actually change just now. Ask me again if you want it to." }]
    const state = settleReply(appendToken([], "I've forgotten that.", ref), "I've forgotten that.", ref, caveats)
    expect(state[0]).toMatchObject({ content: "I've forgotten that.", caveats })
  })

  it('takes only well-formed caveats from the frame', () => {
    expect(readCaveats([{ family: 'files', text: 'No file was written.' }, { family: 'apps' }, 'x', null])).toEqual([
      { family: 'files', text: 'No file was written.' },
    ])
    expect(readCaveats(undefined)).toEqual([])
  })

  it('still lands a reply that streamed no tokens, under a local id', () => {
    const state = settleReply([], 'Answer.', ids())
    expect(state).toHaveLength(1)
    expect(state[0].content).toBe('Answer.')
    expect(state[0].id).toBeLessThan(0) // not saved as far as this window knows
  })
})

describe('interrupt', () => {
  const run = { name: 'files_write', result: 'Wrote plan.md (120 chars).', ok: true, code: '' }

  it('drops the words of a cut-off answer but keeps what she did', () => {
    const ref = ids()
    let state = attachTool([], run, ref)
    state = appendToken(state, 'I wrote the pl', ref)
    state = interrupt(state)
    expect(state).toHaveLength(1)
    expect(state[0]).toMatchObject({ content: '', streaming: false, cut: true })
    expect(state[0].tools).toEqual([run])
  })

  it('removes a cut-off answer that had only words', () => {
    expect(interrupt(appendToken([], 'Half a sen', ids()))).toEqual([])
  })

  it('lets asking again carry on in the answer that was cut off', () => {
    const ref = ids()
    const cut = interrupt(attachTool([], run, ref))
    const state = settleReply(appendToken(cut, 'Done.', ref), 'Done.', ref)
    expect(state).toHaveLength(1)
    expect(state[0]).toMatchObject({ content: 'Done.', streaming: false, cut: false })
    expect(state[0].tools).toEqual([run])
  })
})

describe('attachCitations', () => {
  it('attaches to the last assistant turn', () => {
    const state = attachCitations([assistant({ content: 'See [1].' })], [
      { n: 1, title: 'notes.md', ordinal: 0, text: 'the passage' },
    ])
    expect(state[0].citations).toHaveLength(1)
    expect(state[0].citations[0].text).toBe('the passage')
  })

  it('ignores an empty list and a trailing user turn', () => {
    const before = [assistant()]
    expect(attachCitations(before, [])).toBe(before)
    const user: ChatMessage = { id: 2, role: 'user', content: 'q', citations: [], tools: [] }
    expect(attachCitations([user], [{ n: 1, title: 't', ordinal: 0 }])).toEqual([user])
  })
})

describe('foldStoredMessages', () => {
  const row = (over: Partial<WireMessage>): WireMessage => ({
    id: 1,
    role: 'user',
    content: '',
    tool_name: null,
    tool_ok: null,
    citations: [],
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  })

  it('brings back what a stored answer said she did and she did not', () => {
    const caveats = [{ family: 'memory', text: "My memory didn't actually change just now. Ask me again if you want it to." }]
    const [answer] = foldStoredMessages([row({ id: 3, role: 'assistant', content: 'Done.', caveats })])
    expect(answer.caveats).toEqual(caveats)
  })

  it('folds flat tool rows into the reply they produced', () => {
    // Stored flat, displayed nested: a reloaded conversation must render the same as
    // the live one.
    const folded = foldStoredMessages([
      row({ id: 1, role: 'user', content: 'read it' }),
      row({ id: 2, role: 'tool', content: 'file body', tool_name: 'files_read', tool_ok: true }),
      row({ id: 3, role: 'assistant', content: 'It says hello.' }),
    ])
    expect(folded.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(folded[0].tools).toHaveLength(0)
    expect(folded[1].tools.map((t) => t.name)).toEqual(['files_read'])
  })

  it('keeps the actions of a turn that never answered with that turn', () => {
    // A stopped turn stores its question and its tool rows but no answer. Folded
    // into the next answer, they would read as the next question's actions.
    const folded = foldStoredMessages([
      row({ id: 1, role: 'user', content: 'write the plan' }),
      row({ id: 2, role: 'tool', content: 'Wrote plan.md.', tool_name: 'files_write', tool_ok: true }),
      row({ id: 3, role: 'user', content: 'thanks, now the list' }),
      row({ id: 4, role: 'assistant', content: 'Here it is.' }),
    ])
    expect(folded.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant'])
    expect(folded[1]).toMatchObject({ id: 2, content: '', cut: true })
    expect(folded[1].tools.map((t) => t.name)).toEqual(['files_write'])
    expect(folded[3].tools).toHaveLength(0)
  })

  it('keeps the actions of a last turn that never answered', () => {
    const folded = foldStoredMessages([
      row({ id: 1, role: 'user', content: 'save that' }),
      row({ id: 2, role: 'tool', content: 'Saved.', tool_name: 'memory_save', tool_ok: true }),
    ])
    expect(folded.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(folded[1].cut).toBe(true)
  })

  it('ignores roles it does not render', () => {
    expect(foldStoredMessages([row({ role: 'system', content: 'prompt' })])).toEqual([])
  })
})
