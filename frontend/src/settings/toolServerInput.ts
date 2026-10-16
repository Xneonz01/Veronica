// What the tool server form reads its fields as: an id made from a name, and the
// variables written one per line.

export const ID = /^[a-z0-9-]{1,32}$/
const VARIABLE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/

/** A name as an id: lowercase, dashes for anything else, at most 32 characters. */
export function idFor(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}

/** The variables as written, one per line: `NAME=value`, or `NAME` alone to keep a
    saved value. The number of the first line that can't be read, instead. */
export function readVariables(text: string, saved: string[]): Record<string, string | null> | number {
  const found: Record<string, string | null> = {}
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim()
    if (!line) continue
    const equals = line.indexOf('=')
    const name = (equals === -1 ? line : line.slice(0, equals)).trim()
    if (!VARIABLE.test(name) || (equals === -1 && !saved.includes(name))) return index + 1
    found[name] = equals === -1 ? null : line.slice(equals + 1)
  }
  return found
}
