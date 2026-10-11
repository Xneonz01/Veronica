// Every `styles.name` a component reads has to exist in its CSS module. Vite
// types a module as a string index, so a misspelt or deleted class compiles,
// puts `undefined` in the className, and the element silently loses its style.
// The sheets are read from disk: inside the test runner a CSS module import
// comes back as a proxy of class names, not as text.

import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const src = resolve(import.meta.dirname, '..', '..', 'src')
const components = readdirSync(src, { recursive: true, encoding: 'utf8' }).filter(
  (file) => file.endsWith('.tsx') && !file.includes('__tests__'),
)

const IMPORT = /import\s+(\w+)\s+from\s+'(\.{1,2}\/[^']+\.module\.css)'/g

function problemsIn(file: string): string[] {
  const path = join(src, file)
  const source = readFileSync(path, 'utf8')
  const problems: string[] = []
  for (const [, binding, sheetPath] of source.matchAll(IMPORT)) {
    const sheetFile = resolve(dirname(path), sheetPath)
    let sheet: string
    try {
      sheet = readFileSync(sheetFile, 'utf8')
    } catch {
      problems.push(`${file}: ${sheetPath} not found`)
      continue
    }
    const used = new Set(
      Array.from(source.matchAll(new RegExp(`\\b${binding}\\.(\\w+)`, 'g')), (match) => match[1]),
    )
    for (const name of used) {
      if (!new RegExp(`\\.${name}(?![\\w-])`).test(sheet)) {
        problems.push(`${relative(src, sheetFile)} has no .${name} (read in ${file})`)
      }
    }
  }
  return problems
}

describe('CSS modules', () => {
  it('finds the components it checks', () => {
    expect(components.length).toBeGreaterThan(20)
  })

  it('defines every class a component reads', () => {
    expect(components.flatMap(problemsIn)).toEqual([])
  })
})
