import { describe, expect, it } from 'vitest'
import { capabilityRequest, fromToolServer, toolRunTitle } from '../capabilities'

const IDENTIFIER = /\b[a-z]+[._][a-z_]+\b/

describe('capability names', () => {
  it('names what a request is for in words, in either spelling', () => {
    expect(capabilityRequest('files.write')).toBe('Write a file')
    expect(capabilityRequest('apps_open')).toBe('Open an app')
  })

  it('titles a finished run by its outcome', () => {
    expect(toolRunTitle('files_search', true)).toBe('Searched your files')
    expect(toolRunTitle('files.search', false)).toBe("Couldn't search your files")
  })

  it('names correcting and forgetting in words, as a request and as a result', () => {
    expect(capabilityRequest('memory.forget')).toBe('Forget something')
    expect(toolRunTitle('memory_forget', true)).toBe('Forgot something')
    expect(toolRunTitle('memory_correct', true)).toBe('Corrected what I remember')
    expect(toolRunTitle('memory_correct', false)).toBe("Couldn't correct that")
  })

  it('never shows an identifier, even for a tool it has not heard of', () => {
    const names = ['files_read', 'files.write', 'memory_save', 'memory_forget', 'future.tool', 'something_new']
    for (const name of names) {
      for (const text of [capabilityRequest(name), toolRunTitle(name, true), toolRunTitle(name, false)]) {
        expect(text).not.toMatch(IDENTIFIER)
      }
    }
  })
})

describe('a tool server tool', () => {
  it('is named as one in either spelling, never by its raw name', () => {
    expect(capabilityRequest('mcp.notes.add_note')).toBe('Use a tool from a tool server')
    expect(toolRunTitle('mcp_notes_add_note', true)).toBe('Used a tool from a tool server')
    expect(toolRunTitle('mcp_notes_add_note', false)).toBe("A tool server's tool didn't work")
    expect(fromToolServer('mcpish_tool')).toBe(false)
  })
})
