import { describe, expect, it } from 'vitest'
import { UNIT_SPACE } from '../../shell/memorySize'
import { wireError } from '../callErrors'

describe('the words for a call error', () => {
  it('names a chat model that is not installed, and keeps the call up (bug 30)', () => {
    expect(wireError('llm_model_missing', { model: 'qwen3.5:9b' })).toEqual({
      kind: 'llm_model_missing',
      severity: 'recoverable',
      message: 'I can\'t think right now: my language model, qwen3.5:9b, isn\'t installed. Install it with "ollama pull qwen3.5:9b", then try again.',
    })
  })

  it('names the models a call cannot start without, and how to install them (bug 44)', () => {
    expect(wireError('models_missing', { models: 'embedding' })).toEqual({
      kind: 'models_missing',
      severity: 'fatal',
      message:
        "I can't start the call: my memory model isn't installed. Install it with uv run python -m scripts.install_models in the backend folder, then restart the backend and call again.",
    })
    expect(wireError('models_missing', { models: 'tts,stt,embedding' }).message).toContain(
      "speech synthesis, speech recognition and my memory model aren't installed. Install them with",
    )
    // A code this app has no words for is left unnamed, never shown.
    const unknown = wireError('models_missing', { models: 'constructor,made_up' }).message
    expect(unknown).toContain("some of my models aren't installed")
    expect(unknown).not.toMatch(/constructor|made_up/)
     // One it can name beside one it can't: not called the only one.
    expect(wireError('models_missing', { models: 'stt,made_up' }).message).toContain(
      "some of my models aren't installed. Install them with",
    )
  })

  it('ends a call whose model cannot use tools, and keeps one whose model could not start (bug 45)', () => {
    const noTools = wireError('llm_no_tools', { model: 'smollm:135m' })
    expect(noTools.severity).toBe('fatal')
    expect(noTools.message).toContain("my language model, smollm:135m, can't use tools")
    const cantRun = wireError('llm_cant_run', { model: '<img src=x>' })
    expect(cantRun.severity).toBe('recoverable')
    expect(cantRun.message).toContain("Ollama couldn't start my language model, usually because")
    expect(cantRun.message).not.toContain('<img')
  })

  it('never shows a model name of another shape', () => {
    for (const model of ['<img src=x>', 'rm -rf', undefined, 42]) {
      const { message } = wireError('llm_model_missing', { model })
      expect(message).toContain('the language model my configuration names (llm.model)')
      if (typeof model === 'string') expect(message).not.toContain(model)
    }
  })
})

describe('a call the graphics card has no room for (bug 46)', () => {
  const plain = (text: string) => text.replaceAll(UNIT_SPACE, ' ')

  it('is refused with what it needs and what is free, the need rounded up and the room down', () => {
    // 4,660 MiB and 4,700 both round to 4.6 GB: rounded this way, the need reads larger.
    const full = wireError('card_full', { free_mib: '4660', needs_mib: '4700' })
    expect(full.severity).toBe('fatal')
    expect(plain(full.message)).toBe(
      "I can't start the call: the graphics card is nearly full. A call needs 4.6 GB of it free, and 4.5 GB is. Close a program that's using it, like a game, then call again.",
    )
    expect(plain(wireError('card_full', { free_mib: '0', needs_mib: '11700' }).message)).toContain(
      'A call needs 11.5 GB of it free, and 0 MB is.',
    )
  })

  it('leaves the figures out when they are not sizes', () => {
    for (const [free, needs] of [['4650', undefined], ['-1', '4700'], ['4.5', '4700'], [4650, 4700], ['1e9', '4700']]) {
      const { message } = wireError('card_full', { free_mib: free, needs_mib: needs })
      expect(message).toBe(
        "I can't start the call: the graphics card is nearly full. Close a program that's using it, like a game, then call again.",
      )
    }
  })

  it('says the card is too small, not to close a game, when nothing closed would help', () => {
    // 12,282 MiB is 11.99 GB: what the card has is never rounded up to what it hasn't got.
    const small = wireError('card_small', { total_mib: '12282', needs_mib: '13467' })
    expect(small.severity).toBe('fatal')
    expect(plain(small.message)).toBe(
      "I can't start the call: this graphics card is too small for one. A call needs 13.2 GB of graphics memory, and it has 11.9 GB. A smaller language model may fit: set llm.model in my configuration to one, then restart my backend.",
    )
    expect(small.message).not.toContain('Close a program')
    expect(wireError('card_small', { total_mib: 12288, needs_mib: '13467' }).message).not.toMatch(/GB/)
  })

  it('warns, and keeps the call up, when the card is only nearly full', () => {
    const tight = wireError('card_tight', { free_mib: '5300', needs_mib: '5100' })
    expect(tight.severity).toBe('recoverable')
    expect(tight.message).toBe(
      "The graphics card is nearly full, so I may be slow to answer. Closing a program that's using it, like a game, would help.",
    )
  })
})
