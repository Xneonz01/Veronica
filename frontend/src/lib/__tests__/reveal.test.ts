// What renders as nothing is shown, the same rule the backend uses for a prompt, and
// ordinary text, emoji and line breaks are left as they are.

import { describe, expect, it } from 'vitest'
import { REVEALED, reveal } from '../reveal'

const tags = (text: string) => Array.from(text, (char) => String.fromCodePoint(0xe0000 + char.charCodeAt(0))).join('')

describe('revealing what renders as nothing', () => {
  it('shows a bidi override where it sits, so a name reads as it is', () => {
    expect(reveal('Notes_\u202etxt.bat')).toBe('Notes_⟪U+202E⟫txt.bat')
  })

  it('shows tag characters, zero-width spaces, controls and private use', () => {
    expect(reveal(`ok${tags('hi')}`)).toBe('ok⟪U+E0068⟫⟪U+E0069⟫')
    expect(reveal('re\u200bport\u0007\ue000')).toBe('re⟪U+200B⟫port⟪U+0007⟫⟪U+E000⟫')
  })

  it('shows a line separator and a lone carriage return, and drops one before a line break', () => {
    expect(reveal('a\u2028b\rc\r\nd')).toBe('a⟪U+2028⟫b⟪U+000D⟫c\nd')
  })

  it('leaves text, tabs, line breaks and a joined emoji alone', () => {
    const family = '👩\u200d👩\u200d👧'
    expect(reveal(`Tram times\tin Lisbon\n${family} café`)).toBe(`Tram times\tin Lisbon\n${family} café`)
  })

  it('shows a zero-width joiner that joins nothing', () => {
    expect(reveal('a\u200db')).toBe('a⟪U+200D⟫b')
  })

  it('changes nothing it already revealed, and says when it revealed something', () => {
    const once = reveal('x\u202ey')
    expect(reveal(once)).toBe(once)
    expect(REVEALED.test(once)).toBe(true)
    expect(REVEALED.test('plain words')).toBe(false)
  })
})

describe('what stays part of the text', () => {
  it('shows a run of variation selectors but keeps an emoji’s own form', () => {
    // (mutation: keep every variation selector)
    const run = Array.from('save Alex', (c) => String.fromCodePoint(0xfe00 + (c.charCodeAt(0) % 16)))
    const marks = run.map((c) => `⟪U+${(c.codePointAt(0) ?? 0).toString(16).toUpperCase()}⟫`).join('')
    expect(reveal(`ok \u{1f600}${run.join('')}`)).toBe(`ok \u{1f600}${marks}`)
    expect(reveal(`ok \u2764\ufe0f${run.join('')}`)).toBe(`ok \u2764\ufe0f${marks}`)
    expect(reveal('\u2764\ufe0f\u200d\u{1f525} \u8fbb\u{e0100}')).toBe('\u2764\ufe0f\u200d\u{1f525} \u8fbb\u{e0100}')
  })

  it('keeps a joiner in a word of a script that needs it, and shows one between Latin letters', () => {
    const persian = '\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645'
    expect(reveal(persian)).toBe(persian)
    expect(reveal('pass\u200cword')).toBe('pass⟪U+200C⟫word')
  })

  it('shows blank letters and the grapheme joiner', () => {
    // (mutation: keep the grapheme joiner, a mark that renders as nothing)
    expect(reveal('a\u3164b\u034fc')).toBe('a⟪U+3164⟫b⟪U+034F⟫c')
  })
})

describe('a variation selector', () => {
  it('is shown where it selects nothing, spread one per letter as much as in a run', () => {
    // (mutation: keep one after any visible character)
    expect(reveal('a\ufe00b\ufe01c')).toBe('a⟪U+FE00⟫b⟪U+FE01⟫c')
  })

  it('stays where it picks a form: an emoji, a symbol, a keycap, an ideograph', () => {
    for (const text of ['\u203c\ufe0f', '\u2139\ufe0f', '\u2764\ufe0f', '#\ufe0f\u20e3', '1\ufe0f\u20e3', '\u2229\ufe00', '\u8fbb\u{e0100}']) {
      expect(reveal(text)).toBe(text)
    }
  })
})

describe('a variation selector counts only in a sequence Unicode registers', () => {
  it('is shown after a keycap base with no keycap mark, an ASCII symbol, ideographic punctuation, or an emoji it gives no form', () => {
    // (mutations: any digit; any symbol; any ideographic character; any selector after an
    // emoji, an ideograph, a Myanmar or a Phags-pa letter)
    const dropped: Array<[string, string]> = [
      ['1\ufe0f', '1⟪U+FE0F⟫'],
      ['+\ufe00', '+⟪U+FE00⟫'],
      ['a=\ufe01b', 'a=⟪U+FE01⟫b'],
      ['\u3000\u{e0100}', '\u3000⟪U+E0100⟫'],
      ['\u3007\u{e0100}', '\u3007⟪U+E0100⟫'],
      ['\u{1f600}\ufe03', '\u{1f600}⟪U+FE03⟫'],
      ['+\ufe0f', '+⟪U+FE0F⟫'],
      ['\u8279\ufe03', '\u8279⟪U+FE03⟫'],
      ['\u1000\ufe01', '\u1000⟪U+FE01⟫'],
      ['\ua840\ufe02', '\ua840⟪U+FE02⟫'],
    ]
    for (const [text, shown] of dropped) expect(reveal(text)).toBe(shown)
  })

  it('stays in the registered CJK, Myanmar and Phags-pa variants', () => {
    for (const text of ['\u8279\ufe00', '\u1000\ufe00', '\ua840\ufe01']) expect(reveal(text)).toBe(text)
  })
})

describe('a joiner beside what renders as nothing', () => {
  it('is shown, and an emoji form counts only before the joiner and only where it stays', () => {
    // (mutations: count a selector or the grapheme joiner as a letter; let an emoji form
    // count after the joiner, or before it when it selects nothing)
    expect(reveal('x\u200c\ufe00')).toBe('x⟪U+200C⟫⟪U+FE00⟫')
    expect(reveal('x\u200c\u034f')).toBe('x⟪U+200C⟫⟪U+034F⟫')
    expect(reveal('\u2605\ufe0f\u200d\ufe0f')).toBe('\u2605\ufe0f⟪U+200D⟫⟪U+FE0F⟫')
    expect(reveal('a\ufe0f\u200d\u2605')).toBe('a⟪U+FE0F⟫⟪U+200D⟫\u2605')
  })

  it('shows the marks that render as nothing', () => {
    // (mutation: leave the Khmer and Mongolian marks out of BLANK)
    expect(reveal('a\u17b4\u180bb')).toBe('a⟪U+17B4⟫⟪U+180B⟫b')
  })
})
