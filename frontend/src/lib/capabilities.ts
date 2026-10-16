// What her tools are called on screen, in her own voice. Their names are
// identifiers and never reach the interface; an unknown one gets a plain fallback
// rather than its raw name.
//
// Names come in two spellings: the kernel's ("files.write", in permission
// requests) and the wire form the model calls and the chat stores ("files_write").
// Both are read as the wire form.

const ASKING: Record<string, string> = {
  files_read: 'Read a file',
  files_list: 'Look in a folder',
  files_search: 'Search your files',
  files_write: 'Write a file',
  apps_open: 'Open an app',
  memory_save: 'Remember something',
  memory_recall: 'Look through my memory',
  memory_correct: 'Correct something I remember',
  memory_forget: 'Forget something',
  web_search: 'Search the web',
  web_read: 'Read a web page',
}

const DONE: Record<string, string> = {
  files_read: 'Read a file',
  files_list: 'Looked in a folder',
  files_search: 'Searched your files',
  files_write: 'Wrote a file',
  apps_open: 'Opened an app',
  memory_save: 'Remembered',
  memory_recall: 'Checked my memory',
  memory_correct: 'Corrected what I remember',
  memory_forget: 'Forgot something',
  web_search: 'Searched the web',
  web_read: 'Read a web page',
}

const TRIED: Record<string, string> = {
  files_read: "Couldn't read the file",
  files_list: "Couldn't look in the folder",
  files_search: "Couldn't search your files",
  files_write: "Couldn't write the file",
  apps_open: "Couldn't open the app",
  memory_save: "Couldn't remember that",
  memory_recall: "Couldn't check my memory",
  memory_correct: "Couldn't correct that",
  memory_forget: "Couldn't forget that",
  web_search: "Couldn't search the web",
  web_read: "Couldn't read the page",
}

function wire(name: string): string {
  return name.replace(/\./g, '_')
}

/** A tool a tool server offers ("mcp.notes.add_note"). Its prompt and its card name
    the server; here it is only one of those. */
export function fromToolServer(name: string): boolean {
  return wire(name).startsWith('mcp_')
}

/** The action a permission request is for, as a short imperative. */
export function capabilityRequest(name: string): string {
  if (fromToolServer(name)) return 'Use a tool from a tool server'
  return ASKING[wire(name)] ?? 'Use one of my tools'
}

/** A finished tool run, as a short past-tense title. */
export function toolRunTitle(name: string, ok: boolean): string {
  const key = wire(name)
  if (fromToolServer(name)) return ok ? 'Used a tool from a tool server' : "A tool server's tool didn't work"
  if (!ok) return TRIED[key] ?? "A tool didn't work"
  return DONE[key] ?? 'Used one of my tools'
}
