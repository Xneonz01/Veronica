// What she reads reaches the screen only as text. An answer, a page, a document's title
// or a tool's output can hold markup an attacker wrote, and React escapes it as long as
// nothing hands it to the browser as HTML or code. None of the app does; this keeps it
// so. A real need for one of these is a decision to write down, not a line to add.

import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const src = resolve(import.meta.dirname, '..', '..', 'src')
const files = readdirSync(src, { recursive: true, encoding: 'utf8' }).filter(
  (file) => /\.tsx?$/.test(file) && !file.includes('__tests__'),
)

// Each turns a string into markup or code, or sends the window somewhere a string says.
const SINKS: [string, RegExp][] = [
  ['dangerouslySetInnerHTML', /dangerouslySetInnerHTML/],
  ['innerHTML / outerHTML', /\.(?:inner|outer)HTML\b/],
  ['insertAdjacentHTML', /insertAdjacentHTML/],
  ['document.write', /document\.write/],
  ['a parsed fragment', /createContextualFragment|DOMParser|srcdoc/],
  ['eval', /\beval\s*\(/],
  ['new Function', /new\s+Function\s*\(/],
  ['code in a timer string', /set(?:Timeout|Interval)\s*\(\s*['"`]/],
  ['a link built from data', /href=\{/],
  ['navigating to a string', /window\.open\s*\(|(?:window|document)\.location\s*=(?!=)|\blocation\.href\s*=(?!=)|\blocation\.(?:assign|replace)\s*\(/],
]

function found(source: string): string[] {
  return SINKS.filter(([, pattern]) => pattern.test(source)).map(([name]) => name)
}

describe('what reaches the screen', () => {
  it('is never handed to the browser as markup or code', () => {
    const problems = files.flatMap((file) =>
      found(readFileSync(join(src, file), 'utf8')).map((sink) => `${file}: ${sink}`),
    )
    expect(problems).toEqual([])
  })

  it('would be caught if it were', () => {
    // The check itself, on lines that each open one door.
    expect(found('<div dangerouslySetInnerHTML={{ __html: answer }} />')).toEqual(['dangerouslySetInnerHTML'])
    expect(found('node.innerHTML = page')).toEqual(['innerHTML / outerHTML'])
    expect(found("setTimeout('run()', 10)")).toEqual(['code in a timer string'])
    expect(found('<a href={hit.url}>')).toEqual(['a link built from data'])
    expect(found('window.location = target')).toEqual(['navigating to a string'])
    expect(found('location.assign(next)')).toEqual(['navigating to a string'])
    expect(found('if (location === here)')).toEqual([])
    expect(found('const location = useLocation()')).toEqual([])
  })
})
