// What a tool returned, as its card shows it. Most results are already plain
// sentences. A memory recall comes back framed for the model: a heading line, an
// instruction about using your name, and labelled lines ("Recall — …"). The card
// shows only what she found, in words meant for you.

const NAME = /^The user's name is ([^.;]+)[.;]/

// Saved by earlier versions, a file result is the text written for the model: a
// warning line, then the content between markers tagged with a one-time code.
const FENCED = /^Result of [^\n]*\n<<result [^\n]*>>\n([\s\S]*)\n<<end result [0-9a-f]+>>$/

export function presentResult(name: string, result: string): string {
  const fenced = FENCED.exec(result)
  if (fenced) return fenced[1]
  if (name.replace(/\./g, '_') !== 'memory_recall') return result
  const lines = result.split('\n').map((line) => line.trim())
  const found = lines.flatMap((line, index) => {
    // The heading ("What you remember about the user, …:") is for the model.
    if (index === 0 && line.endsWith(':')) return []
    if (line.startsWith('Do not address the user')) return []
    const named = NAME.exec(line)
    if (named) return [`Your name is ${named[1]}.`]
    const labelled = /^(?:Recall|Profile) — (.+)$/.exec(line)
    if (labelled) return [labelled[1]]
    return line ? [line] : []
  })
  return found.length > 0 ? found.join('\n') : 'Nothing I remember fits what you asked.'
}
