import { describe, expect, it } from 'vitest'
import { parseInline, plainMath } from '../inline'

describe('parseInline', () => {
  it('reads bold, italic and code', () => {
    expect(parseInline('**Monday**: the *quarterly* report, in `report.md`.')).toEqual([
      { type: 'strong', children: [{ type: 'text', text: 'Monday' }] },
      { type: 'text', text: ': the ' },
      { type: 'em', children: [{ type: 'text', text: 'quarterly' }] },
      { type: 'text', text: ' report, in ' },
      { type: 'code', text: 'report.md' },
      { type: 'text', text: '.' },
    ])
  })

  it('leaves arithmetic and snake_case alone', () => {
    expect(parseInline('5 * 3 = 15 and 2 * 4 = 8 in file_name_here')).toEqual([
      { type: 'text', text: '5 * 3 = 15 and 2 * 4 = 8 in file_name_here' },
    ])
  })

  it('turns a marker into a citation only when the source exists', () => {
    // A marker stays with the word before it and the punctuation after it, so it
    // never starts a line of its own.
    expect(parseInline('Start on the 30th [1], before the rain [2].', new Set([1]))).toEqual([
      { type: 'text', text: 'Start on the ' },
      {
        type: 'keep',
        children: [
          { type: 'text', text: '30th ' },
          { type: 'cite', n: 1 },
          { type: 'text', text: ',' },
        ],
      },
      { type: 'text', text: ' before the rain [2].' },
    ])
    expect(parseInline('Both say so [1][2].', new Set([1, 2]))).toContainEqual({
      type: 'keep',
      children: [
        { type: 'text', text: 'so ' },
        { type: 'cite', n: 1 },
        { type: 'cite', n: 2 },
        { type: 'text', text: '.' },
      ],
    })
  })

  it('reads a link and keeps its address', () => {
    expect(parseInline('See [the timetable](https://example.com/trains).')).toContainEqual({
      type: 'link',
      text: 'the timetable',
      url: 'https://example.com/trains',
    })
  })

  it('keeps the line breaks the writer meant', () => {
    expect(parseInline('12 Harbour Row\nSeaton')).toEqual([
      { type: 'text', text: '12 Harbour Row' },
      { type: 'break' },
      { type: 'text', text: 'Seaton' },
    ])
  })

  it('does not touch math-looking text inside code', () => {
    expect(parseInline('Run `echo $HOME \\times $PATH`')).toContainEqual({ type: 'code', text: 'echo $HOME \\times $PATH' })
  })
})

describe('plainMath', () => {
  it('turns inline LaTeX into plain text', () => {
    expect(plainMath('That is $4.00 \\times 3 = 12.00$ in total.')).toBe('That is 4.00 × 3 = 12.00 in total.')
    expect(plainMath('\\( \\frac{1}{2} + \\frac{a+b}{c} \\)')).toBe('1/2 + (a+b)/c')
    expect(plainMath('$$\\text{Total} = 3 \\cdot 4$$')).toBe('Total = 3 · 4')
    expect(plainMath('Area is $x^2 + y^{10}$.')).toBe('Area is x² + y¹⁰.')
    expect(plainMath('It comes to $2{,}150 + 180 = 2{,}330$.')).toBe('It comes to 2,150 + 180 = 2,330.')
  })

  it('leaves prices alone', () => {
    expect(plainMath('It costs $4.00 and $5.00 with delivery.')).toBe('It costs $4.00 and $5.00 with delivery.')
  })
})
