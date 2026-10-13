// A build step: fails when the built page doesn't open with its Content-Security-Policy
// (src/lib/csp.ts), or holds something that policy would refuse and so break in the
// app: an inline script, style or event handler, or an asset inlined as a data: address.

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CONTENT_SECURITY_POLICY } from '../src/lib/csp.ts'

const dist = join(import.meta.dirname, '..', 'dist')
const page = readFileSync(join(dist, 'index.html'), 'utf8')
const problems: string[] = []

const head = page.slice(page.indexOf('<head>') + '<head>'.length).trimStart()
const policy = /^<meta http-equiv="Content-Security-Policy" content="([^"]*)">/.exec(head)
if (policy?.[1].replaceAll('&#39;', "'") !== CONTENT_SECURITY_POLICY) {
  problems.push('the page does not open with its Content-Security-Policy')
}
if (/<script\b(?![^>]*\ssrc=)[^>]*>/i.test(page)) problems.push('an inline script')
if (/<style\b|\sstyle=|\son[a-z]+=/i.test(page)) problems.push('an inline style or event handler')
for (const name of readdirSync(join(dist, 'assets')).filter((file) => file.endsWith('.css'))) {
  if (/url\(\s*['"]?data:/i.test(readFileSync(join(dist, 'assets', name), 'utf8'))) {
    problems.push(`${name} inlines an asset as a data: address`)
  }
}

if (problems.length > 0) {
  console.error(`The built page would break under its policy: ${problems.join('; ')}`)
  process.exit(1)
}
console.log('Page: opens with its policy, and holds nothing it would refuse.')
