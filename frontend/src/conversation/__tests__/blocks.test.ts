import { describe, expect, it } from 'vitest'
import { parseBlocks, type Block, type ListBlock } from '../blocks'

const kinds = (blocks: Block[]) => blocks.map((block) => block.type)

describe('parseBlocks', () => {
  it('keeps the line breaks inside a paragraph and splits paragraphs on blank lines', () => {
    expect(parseBlocks('Dear Rowan,\nThanks for the photos.\n\nSee you soon.')).toEqual([
      { type: 'paragraph', text: 'Dear Rowan,\nThanks for the photos.' },
      { type: 'paragraph', text: 'See you soon.' },
    ])
  })

  it('reads a numbered list with blank lines between items as one list', () => {
    // Answered this way, a parser that makes one list per item numbers every
    // item "1.".
    const [list] = parseBlocks('1. **Monday**: the report.\n\n2. **Tuesday**: the review.\n\n3. **Thursday**: dentist.') as ListBlock[]
    expect(list).toMatchObject({ type: 'list', ordered: true, start: 1 })
    expect(list.items.map((item) => item.text)).toEqual([
      '**Monday**: the report.',
      '**Tuesday**: the review.',
      '**Thursday**: dentist.',
    ])
  })

  it('keeps the number a list starts at', () => {
    const [list] = parseBlocks('4. Book the roofer\n5. Pay the deposit') as ListBlock[]
    expect(list.start).toBe(4)
  })

  it('nests an indented list under its item and goes back to the outer list after', () => {
    const [list] = parseBlocks('- Groceries\n  - lemons\n  - film\n- Laundry') as ListBlock[]
    expect(list.items.map((item) => item.text)).toEqual(['Groceries', 'Laundry'])
    expect(list.items[0].children?.items.map((item) => item.text)).toEqual(['lemons', 'film'])
  })

  it('continues an item with the lines that belong to it, and ends the list at a paragraph', () => {
    const blocks = parseBlocks('- Lemon tree\n  for the balcony\n\nThat should do it.')
    expect(kinds(blocks)).toEqual(['list', 'paragraph'])
    expect((blocks[0] as ListBlock).items[0].text).toBe('Lemon tree\nfor the balcony')
  })

  it('starts a list right after an introducing line', () => {
    expect(kinds(parseBlocks("Here's the plan:\n- rest the starter\n- feed it at night"))).toEqual(['paragraph', 'list'])
  })

  it('reads fenced code, and one that is still streaming', () => {
    expect(parseBlocks('```python\nprint("hi")\n```')).toEqual([
      { type: 'code', lang: 'python', text: 'print("hi")', open: false },
    ])
    expect(parseBlocks('Try this:\n```bash\nls -la')).toEqual([
      { type: 'paragraph', text: 'Try this:' },
      { type: 'code', lang: 'bash', text: 'ls -la', open: true },
    ])
  })

  it('does not read markdown inside code', () => {
    const [code] = parseBlocks('```\n# not a heading\n- not a list\n```')
    expect(code).toMatchObject({ type: 'code', text: '# not a heading\n- not a list' })
  })

  it('reads headings, quotes and rules', () => {
    expect(parseBlocks('## Next steps\n> Measure twice.\n\n---')).toEqual([
      { type: 'heading', level: 2, text: 'Next steps' },
      { type: 'quote', text: 'Measure twice.' },
      { type: 'rule' },
    ])
  })

  it('reads a pipe table with its alignment, padding short rows', () => {
    const [table] = parseBlocks('| Roofer | Price | Start |\n|:---|---:|:---:|\n| Ash & Sons | £2,400 | 30th |\n| Birch | £2,150 |')
    expect(table).toEqual({
      type: 'table',
      header: ['Roofer', 'Price', 'Start'],
      align: ['left', 'right', 'center'],
      rows: [
        ['Ash & Sons', '£2,400', '30th'],
        ['Birch', '£2,150', ''],
      ],
    })
  })
})
