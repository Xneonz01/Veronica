// Model output as a small, safe document: the block level. Hand-written, because
// a general markdown library carries either an HTML pass (an injection surface
// for text built from your files) or more weight than this whole screen. Nothing
// here produces markup; RichText.tsx turns the tree into React elements.
//
// It reads what the model actually writes: paragraphs whose single line breaks
// are meant, headings, lists (tight or loose, numbered or not, nested), fenced
// code (still open while it streams), quotes, pipe tables and rules.

export type Align = 'left' | 'center' | 'right' | null
export type ListItem = { text: string; children: ListBlock | null }
export type ListBlock = { type: 'list'; ordered: boolean; start: number; items: ListItem[] }

export type Block =
  | { type: 'paragraph'; text: string }
  | { type: 'heading'; level: number; text: string }
  | ListBlock
  /** `open`: the closing fence hasn't arrived yet. */
  | { type: 'code'; lang: string; text: string; open: boolean }
  | { type: 'quote'; text: string }
  | { type: 'table'; header: string[]; align: Align[]; rows: string[][] }
  | { type: 'rule' }

const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/
const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const ITEM = /^(\s*)([-*+•]|\d{1,9}[.)])\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let at = 0
  while (at < lines.length) {
    const line = lines[at]
    if (!line.trim()) {
      at += 1
      continue
    }
    const fence = FENCE.exec(line)
    if (fence) {
      const body: string[] = []
      at += 1
      while (at < lines.length && !isClosing(lines[at], fence[1])) body.push(lines[at++])
      const open = at >= lines.length
      if (!open) at += 1
      blocks.push({ type: 'code', lang: fence[2], text: body.join('\n'), open })
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2] })
      at += 1
      continue
    }
    if (RULE.test(line)) {
      blocks.push({ type: 'rule' })
      at += 1
      continue
    }
    if (isTableRow(line) && at + 1 < lines.length && SEPARATOR.test(lines[at + 1])) {
      const [table, next] = parseTable(lines, at)
      blocks.push(table)
      at = next
      continue
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = []
      while (at < lines.length && QUOTE.test(lines[at])) quoted.push(QUOTE.exec(lines[at++])?.[1] ?? '')
      blocks.push({ type: 'quote', text: quoted.join('\n').trim() })
      continue
    }
    if (ITEM.test(line)) {
      const [list, next] = parseList(lines, at)
      blocks.push(list)
      at = next
      continue
    }
    const paragraph = [line.trim()]
    at += 1
    while (at < lines.length && lines[at].trim() && !startsBlock(lines, at)) paragraph.push(lines[at++].trim())
    blocks.push({ type: 'paragraph', text: paragraph.join('\n') })
  }
  return blocks
}

function isClosing(line: string, fence: string): boolean {
  const trimmed = line.trim()
  return trimmed.length >= fence.length && trimmed === fence[0].repeat(trimmed.length)
}

function startsBlock(lines: string[], at: number): boolean {
  const line = lines[at]
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    ITEM.test(line) ||
    (isTableRow(line) && at + 1 < lines.length && SEPARATOR.test(lines[at + 1]))
  )
}

const indentOf = (line: string) => line.length - line.trimStart().length

/**
 * A list, read the way the model writes it. Items separated by blank lines (a
 * "loose" list) stay one list, so numbering runs on instead of restarting at 1
 * under every item; a deeper-indented item starts a nested list; an indented
 * line, or one straight after an item, continues that item's text.
 */
function parseList(lines: string[], from: number): [ListBlock, number] {
  const first = ITEM.exec(lines[from])
  const base = first ? first[1].length : 0
  const ordered = first !== null && /\d/.test(first[2])
  const list: ListBlock = { type: 'list', ordered, start: ordered && first ? parseInt(first[2], 10) : 1, items: [] }
  let at = from
  let afterBlank = false
  while (at < lines.length) {
    const line = lines[at]
    if (!line.trim()) {
      afterBlank = true
      at += 1
      continue
    }
    const item = ITEM.exec(line)
    const indent = indentOf(line)
    const last = list.items[list.items.length - 1]
    if (item && indent < base) break // an item of the list this one is nested in
    if (item && indent <= base + 1) {
      if (/\d/.test(item[2]) !== ordered) break // a different kind of list begins
      list.items.push({ text: item[3], children: null })
    } else if (item && last) {
      const [child, next] = parseList(lines, at)
      if (last.children) last.children.items.push(...child.items)
      else last.children = child
      at = next
      afterBlank = false
      continue
    } else if (last && (indent > base || (!afterBlank && !startsBlock(lines, at)))) {
      last.text += `\n${line.trim()}`
    } else {
      break
    }
    afterBlank = false
    at += 1
  }
  // Blank lines the list stopped on belong to what follows.
  while (at > from && !lines[at - 1]?.trim()) at -= 1
  return [list, at]
}

function isTableRow(line: string): boolean {
  return /\S\s*\|\s*\S/.test(line) || /^\s*\|.*\|\s*$/.test(line)
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function parseTable(lines: string[], from: number): [Block, number] {
  const header = cells(lines[from])
  const align: Align[] = cells(lines[from + 1]).map((cell) => {
    const left = cell.startsWith(':')
    const right = cell.endsWith(':')
    return left && right ? 'center' : right ? 'right' : left ? 'left' : null
  })
  const rows: string[][] = []
  let at = from + 2
  while (at < lines.length && lines[at].trim() && isTableRow(lines[at])) {
    const row = cells(lines[at++])
    // Short rows are padded and long ones trimmed, so every row lines up.
    rows.push(header.map((_, index) => row[index] ?? ''))
  }
  return [{ type: 'table', header, align: header.map((_, index) => align[index] ?? null), rows }, at]
}
