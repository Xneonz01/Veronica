// The last build step: fails when a stylesheet in the output names an animation
// whose keyframes it never defines (src/lib/keyframes.ts says why that happens).
// All the CSS files are read as one, because keyframes live in the global sheet
// and are used from the per-route ones.

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { findMissingKeyframes } from '../src/lib/keyframes.ts'

const assets = join(import.meta.dirname, '..', 'dist', 'assets')
const css = readdirSync(assets)
  .filter((name) => name.endsWith('.css'))
  .map((name) => readFileSync(join(assets, name), 'utf8'))
  .join('\n')

const missing = findMissingKeyframes(css)
if (missing.length > 0) {
  console.error(`These animations name keyframes that don't exist: ${missing.join(', ')}`)
  process.exit(1)
}
console.log('Animations: every name used has its keyframes.')
