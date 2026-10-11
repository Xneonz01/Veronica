// Model output as a small, safe document: the inline level. Bold, italic, code,
// links, line breaks and citation markers; and LaTeX, which the model slips into
// answers even when told not to, turned into plain text before anything else.

export type Inline =
  | { type: 'text'; text: string }
  | { type: 'strong'; children: Inline[] }
  | { type: 'em'; children: Inline[] }
  | { type: 'code'; text: string }
  | { type: 'cite'; n: number }
  | { type: 'link'; text: string; url: string }
  | { type: 'break' }
  /** Pieces that must not be split across lines: a word with its citations. */
  | { type: 'keep'; children: Inline[] }

// One alternation, earliest match wins. Emphasis needs a word edge on both sides
// and no space just inside, so "5 * 3" and snake_case stay text.
const TOKEN = new RegExp(
  [
    /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/.source, // 1, 2: code
    /\*\*(?!\s)([^\n]+?)(?<!\s)\*\*/.source, // 3: bold
    /(?<![\w_])__(?!\s)([^\n]+?)(?<!\s)__(?![\w_])/.source, // 4: bold
    /(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])/.source, // 5: italic
    /(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])/.source, // 6: italic
    /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/.source, // 7, 8: link
    /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/.source, // 9: citation markers
    /\n/.source, // a line break the writer meant
  ].join('|'),
  'g',
)

/** Inline pieces of `text`; a citation marker counts only for a source that exists. */
export function parseInline(text: string, sources: ReadonlySet<number> = new Set()): Inline[] {
  const out: Inline[] = []
  // Math is cleaned up between code spans only: code keeps its dollars and slashes.
  const source = text
    .split(/(`[^`\n]*`)/)
    .map((part, index) => (index % 2 === 1 ? part : plainMath(part)))
    .join('')
  let cursor = 0
  for (const match of source.matchAll(TOKEN)) {
    const at = match.index ?? 0
    if (at > cursor) out.push({ type: 'text', text: source.slice(cursor, at) })
    out.push(...token(match, sources))
    cursor = at + match[0].length
  }
  if (cursor < source.length) out.push({ type: 'text', text: source.slice(cursor) })
  return merge(out)
}

function token(match: RegExpMatchArray, sources: ReadonlySet<number>): Inline[] {
  if (match[2] !== undefined) return [{ type: 'code', text: match[2].replace(/^ (.*) $/, '$1') }]
  const bold = match[3] ?? match[4]
  if (bold !== undefined) return [{ type: 'strong', children: parseInline(bold, sources) }]
  const italic = match[5] ?? match[6]
  if (italic !== undefined) return [{ type: 'em', children: parseInline(italic, sources) }]
  if (match[7] !== undefined && match[8] !== undefined) return [{ type: 'link', text: match[7], url: match[8] }]
  if (match[9] !== undefined) {
    const numbers = match[9].split(',').map((n) => parseInt(n, 10))
    // A marker for a source that isn't there stays as the model wrote it.
    if (numbers.every((n) => sources.has(n))) return numbers.map((n) => ({ type: 'cite', n }))
    return [{ type: 'text', text: match[0] }]
  }
  return [{ type: 'break' }]
}

/**
 * Adjacent text pieces as one, so the renderer makes fewer nodes; and each run of
 * citations kept together with the word before it and the punctuation after it,
 * so a marker never starts a line away from the words it supports. (A no-break
 * space isn't enough: browsers still break before an inline block.)
 */
function merge(pieces: Inline[]): Inline[] {
  const out: Inline[] = []
  for (const piece of pieces) {
    const last = out[out.length - 1]
    if (piece.type === 'text' && last?.type === 'text') {
      out[out.length - 1] = { type: 'text', text: last.text + piece.text }
    } else if (piece.type === 'cite' && last?.type === 'keep') {
      last.children.push(piece)
    } else if (piece.type === 'cite') {
      const word = last?.type === 'text' ? /\S+\s*$/.exec(last.text) : null
      if (last?.type === 'text' && word) {
        const before = last.text.slice(0, word.index)
        out.splice(out.length - 1, 1, ...(before ? [{ type: 'text' as const, text: before }] : []))
        out.push({ type: 'keep', children: [{ type: 'text', text: word[0].replace(/\s+$/, ' ') }, piece] })
      } else {
        out.push({ type: 'keep', children: [piece] })
      }
    } else if (piece.type === 'text' && last?.type === 'keep') {
      const punctuation = /^[.,;:!?)\]]+/.exec(piece.text)
      if (punctuation) last.children.push({ type: 'text', text: punctuation[0] })
      const rest = piece.text.slice(punctuation?.[0].length ?? 0)
      if (rest) out.push({ type: 'text', text: rest })
    } else {
      out.push(piece)
    }
  }
  return out
}

// ————————————————————————————————— LaTeX to plain text

const SYMBOLS: Record<string, string> = {
  times: '×', div: '÷', cdot: '·', pm: '±', mp: '∓', le: '≤', leq: '≤', ge: '≥', geq: '≥',
  neq: '≠', ne: '≠', approx: '≈', sim: '~', infty: '∞', to: '→', rightarrow: '→', leftarrow: '←',
  Rightarrow: '⇒', Leftarrow: '⇐', ldots: '…', dots: '…', cdots: '⋯', degree: '°', circ: '°',
  pi: 'π', alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', Delta: 'Δ', theta: 'θ', lambda: 'λ',
  mu: 'μ', sigma: 'σ', Sigma: 'Σ', sum: 'Σ', prod: 'Π', int: '∫', partial: '∂', in: '∈',
}

const SUPERSCRIPT: Record<string, string> = {
  '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸',
  '9': '⁹', '+': '⁺', '-': '⁻', '(': '⁽', ')': '⁾', n: 'ⁿ', i: 'ⁱ',
}

/**
 * Math delimiters and commands as plain text: "$4.00 \times 3$" reads "4.00 × 3".
 * A pair of dollar signs counts as math only when what's between them has a
 * command, a superscript, a subscript or LaTeX's {,} thousands separator, so
 * "$4.00 and $5.00" is left alone.
 */
export function plainMath(text: string): string {
  if (!text.includes('$') && !text.includes('\\')) return text
  return text
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, math: string) => latex(math))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, math: string) => latex(math))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, math: string) => latex(math))
    .replace(/\$([^$\n]+?)\$/g, (whole, math: string) => (MATHLIKE.test(math) ? latex(math) : whole))
}

const MATHLIKE = /\\[A-Za-z]+|[\^_]\{?\w|\{,\}/

function latex(math: string): string {
  let text = math
  // Twice over for a fraction inside a fraction.
  for (let pass = 0; pass < 2; pass += 1) {
    text = text.replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, (_, top: string, bottom: string) => `${group(top)}/${group(bottom)}`)
  }
  text = text
    .replace(/\\sqrt\{([^{}]*)\}/g, (_, inner: string) => `√${group(inner)}`)
    .replace(/\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}/g, '$1')
    .replace(/\\left|\\right/g, '')
    .replace(/\\([%$&#_{}])/g, '$1')
    .replace(/\\[,;:! ]/g, ' ')
    .replace(/\^\{([^{}]*)\}|\^(\w)/g, (_, braced: string | undefined, single: string | undefined) => raise(braced ?? single ?? ''))
    .replace(/_\{([^{}]*)\}/g, '_$1')
    .replace(/\\([A-Za-z]+)/g, (_, name: string) => SYMBOLS[name] ?? name)
    .replace(/[{}]/g, '')
  return text.replace(/\s+/g, ' ').trim()
}

/** Brackets around a multi-part fraction side, so "a+b over c" reads (a+b)/c. */
function group(part: string): string {
  const trimmed = part.trim()
  return /[\s+\-*/]/.test(trimmed) ? `(${trimmed})` : trimmed
}

function raise(exponent: string): string {
  const chars = [...exponent.trim()]
  return chars.every((char) => char in SUPERSCRIPT) ? chars.map((char) => SUPERSCRIPT[char]).join('') : `^(${exponent})`
}
