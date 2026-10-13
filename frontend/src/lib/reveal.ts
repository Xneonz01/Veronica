// Characters that render as nothing, made visible. A page, a search result or a tool's
// output can hide text in them: a bidi override makes `Notes_<U+202E>txt.bat` read as
// `Notes_tab.txt`, tag characters can spell a whole sentence nobody sees, and variation
// selectors where they select nothing carry a byte each. The backend reveals what a
// prompt carries by the same rules (`core/text_safety.py`); this is the rule for what
// the app shows of untrusted text itself. Revealing twice changes nothing, so it is safe
// on text the backend already revealed. A joiner inside a word of a script that needs
// one (Persian, the Indic scripts), and a variation selector after an emoji, a symbol
// or an ideograph, picking its form, are part of the text and stay.

const UNSEEN = /^[\p{Cc}\p{Cf}\p{Co}\p{Cn}\p{Cs}\p{Zl}\p{Zp}]$/u
// Letters that render as blank space (the Hangul fillers), and marks that render as
// nothing: the grapheme joiner, the Khmer inherent vowels, the Mongolian free
// variation selectors.
const BLANK = new Set(['\u115f', '\u1160', '\u3164', '\uffa0', '\u034f', '\u17b4', '\u17b5', '\u180b', '\u180c', '\u180d', '\u180f'])
const VARIATION = /^[\u{fe00}-\u{fe0f}\u{e0100}-\u{e01ef}]$/u
// A zero-width joiner between two symbols builds one emoji (a family, a flag); the one
// before it can carry its own emoji form's selector (EMOJI_FORM).
const SYMBOL = /^[\p{So}\p{Sk}\u{1f3fb}-\u{1f3ff}]$/u
const EMOJI_FORM = '\ufe0f'
const LETTER = /^[\p{L}\p{M}]$/u
// What a variation selector may pick a form of, as Unicode registers it (the same rule
// as `_selects` in core/text_safety.py): text or emoji presentation after a symbol beyond
// ASCII, an emoji whose character isn't a symbol (double exclamation, interrobang,
// information, wavy dash, part alternation mark) or a keycap's base before its mark; a
// math variant after a math symbol beyond ASCII; CJK, Myanmar and Phags-pa variants;
// the supplement after a CJK ideograph.
const SYMBOLIC = /^[\p{So}\p{Sk}\p{Sm}]$/u
const MATH = /^\p{Sm}$/u
const EMOJI_BASES = new Set(['\u203c', '\u2049', '\u2139', '\u3030', '\u303d'])
const KEYCAP_BASES = new Set(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '#', '*'])
const KEYCAP = '\u20e3'
const IDEOGRAPH = /^[\p{Unified_Ideograph}\u{f900}-\u{faff}\u{2f800}-\u{2fa1f}]$/u
const ZWJ = '\u200d'
const ZWNJ = '\u200c'
/** How a revealed character looks, so a surface can tell that some were found. */
export const REVEALED = /⟪U\+[0-9A-F]{4,6}⟫/

function shown(char: string): string {
  const code = char.codePointAt(0) ?? 0
  return `⟪U+${code.toString(16).toUpperCase().padStart(4, '0')}⟫`
}

const ascii = (char: string): boolean => (char.codePointAt(0) ?? 0) < 0x80

/** Whether the variation selector `char` picks a registered form of `before`. */
function selects(char: string, before: string, after: string): boolean {
  const code = char.codePointAt(0) ?? 0
  if (code >= 0xe0100) return IDEOGRAPH.test(before)
  if (code >= 0xfe0e) {
    if (KEYCAP_BASES.has(before)) return after === KEYCAP
    return EMOJI_BASES.has(before) || (!ascii(before) && SYMBOLIC.test(before))
  }
  return (
    (MATH.test(before) && !ascii(before)) ||
    (code <= 0xfe02 && IDEOGRAPH.test(before)) ||
    (code === 0xfe00 && before >= '\u1000' && before <= '\u109f') ||
    (code <= 0xfe01 && before >= '\ua840' && before <= '\ua877')
  )
}

// A mark counts as a letter (the virama before a joiner), unless it renders as nothing.
const letter = (char: string): boolean => LETTER.test(char) && !VARIATION.test(char) && !BLANK.has(char)

/** Whether the joiner between `before` and `after` does a joiner's work, never beside
 *  something that renders as nothing. `beforeShown`: the character before it stays. */
function joins(char: string, before: string, after: string, beforeShown: boolean): boolean {
  if (!before || !after) return false
  if (letter(before) && letter(after) && !(ascii(before) && ascii(after))) return true
  return char === ZWJ && SYMBOL.test(after) && (SYMBOL.test(before) || (before === EMOJI_FORM && beforeShown))
}

/** `text` with every character that renders as nothing shown as `⟪U+XXXX⟫`. Line
 *  breaks and tabs stay as they are; a carriage return before a line break goes. */
export function reveal(text: string): string {
  const chars = Array.from(text)
  const hidden: boolean[] = []
  chars.forEach((char, index) => {
    const before = chars[index - 1] ?? ''
    const after = chars[index + 1] ?? ''
    if (char === '\n' || char === '\t' || char === '\r') hidden.push(false)
    else if (BLANK.has(char)) hidden.push(true)
    else if (VARIATION.test(char)) hidden.push(!before || VARIATION.test(before) || hidden[index - 1] || !selects(char, before, after))
    else if (!UNSEEN.test(char)) hidden.push(false)
    else hidden.push(!((char === ZWJ || char === ZWNJ) && joins(char, before, after, !hidden[index - 1])))
  })
  return chars
    .map((char, index) => {
      if (char === '\r') return chars[index + 1] === '\n' ? '' : shown(char)
      return hidden[index] ? shown(char) : char
    })
    .join('')
}
