// @vitest-environment jsdom
// Tool servers in Settings: empty, loading and failed states; a server's state, its
// command in full and its tools; allowing a tool; adding one with its variables never
// shown again; and the change event that keeps every screen current.

import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { idFor, readVariables } from '../toolServerInput'
import { ToolServerSettings } from '../ToolServerSettings'
import { json, serve, unreachable } from './serve'

const lane = vi.hoisted(() => ({ handlers: {} as { onToolServersChanged?: () => void } }))
vi.mock('../../audio/useAppEvents', () => ({
  useAppEvents: (handlers: typeof lane.handlers) => {
    lane.handlers = handlers
  },
}))

const NOTES = {
  id: 'notes',
  name: 'Notes',
  command: 'C:\\Program Files\\nodejs\\npx.cmd',
  args: ['-y', '@example/notes-server'],
  env: ['NOTES_KEY'],
  enabled: true,
  internet: false,
  state: 'running',
  detail: '',
  version: '2026-07-28',
  server_name: 'notes-server',
  tools: [
    { name: 'add', title: 'Add a note', description: 'Adds a note.', state: 'new', unsupported: '', offered: false, arguments: ['text'], pin: 'pin-add' },
    { name: 'find', title: 'Find notes', description: 'Finds notes.', state: 'changed', unsupported: '', offered: false, arguments: [], pin: 'pin-find' },
    { name: 'shape', title: 'shape', description: '', state: 'unsupported', unsupported: "o isn't a string", offered: false, arguments: [], pin: 'pin-shape' },
  ],
}

const listing = (servers: unknown[]) => json({ servers, per_turn: 12, tokens: 1500 })

beforeEach(() => {
  stubTopLayer()
  lane.handlers = {}
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('tool servers', () => {
  it('says there are none yet and offers to add one', async () => {
    serve({ 'GET /mcp': () => listing([]) })
    render(<ToolServerSettings />)
    expect(await screen.findByText('No tool servers yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a tool server' })).toBeTruthy()
  })

  it('says it could not load them, and tries again', async () => {
    unreachable()
    render(<ToolServerSettings />)
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('shows a server running, its command whole and its tools by what they need', async () => {
    serve({ 'GET /mcp': () => listing([NOTES]) })
    render(<ToolServerSettings />)
    const card = await screen.findByRole('article', { name: 'Notes' })
    const inCard = within(card)
    expect(inCard.getByText('Running')).toBeTruthy()
    // quoted: it has a space in it
    expect(inCard.getByText('"C:\\Program Files\\nodejs\\npx.cmd" -y @example/notes-server')).toBeTruthy()
    expect(inCard.getByText('Takes: text')).toBeTruthy()
    expect(inCard.getByText('NOTES_KEY')).toBeTruthy()
    expect(inCard.getByRole('button', { name: 'Allow Add a note' })).toBeTruthy()
    expect(inCard.getByRole('button', { name: 'Allow Find notes' }).textContent).toBe('Allow again')
    expect(inCard.getByText(/Changed since you allowed it/)).toBeTruthy()
    expect(inCard.getByText(/Can't be offered: o isn't a string/)).toBeTruthy()
  })

  it('says why a server is not running', async () => {
    serve({ 'GET /mcp': () => listing([{ ...NOTES, state: 'failed', detail: "There's no program at that path.", tools: [] }]) })
    render(<ToolServerSettings />)
    expect(await screen.findByText('Not running')).toBeTruthy()
    expect(screen.getByText("There's no program at that path.")).toBeTruthy()
  })

  it('allows a tool through the API and shows the answer', async () => {
    const approved = { ...NOTES, tools: [{ ...NOTES.tools[0], state: 'approved', offered: true }] }
    const backend = serve({
      'GET /mcp': () => listing([NOTES]),
      'PUT /mcp/servers/notes/tools/add': () => listing([approved]),
    })
    render(<ToolServerSettings />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Add a note' }))
    expect(await screen.findByRole('button', { name: 'Withdraw Add a note' })).toBeTruthy()
    const [, put] = backend.mock.calls.find(([, init]) => init?.method === 'PUT') ?? []
    expect(JSON.parse(String(put?.body))).toEqual({ approved: true, pin: 'pin-add' }) // the tool as shown
  })

  it('switching a server off keeps its saved variables', async () => {
    const backend = serve({
      'GET /mcp': () => listing([NOTES]),
      'PUT /mcp/servers/notes': (init) => listing([{ ...NOTES, ...JSON.parse(String(init?.body)), env: ['NOTES_KEY'], state: 'off' }]),
    })
    render(<ToolServerSettings />)
    await userEvent.click(await screen.findByRole('switch', { name: 'Run Notes' }))
    await waitFor(() => expect(backend.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true))
    const [, put] = backend.mock.calls.find(([, init]) => init?.method === 'PUT') ?? []
    expect(JSON.parse(String(put?.body))).toMatchObject({ enabled: false, env: { NOTES_KEY: null } })
  })

  it('adds a server, its id made from its name', async () => {
    const backend = serve({
      'GET /mcp': () => listing([]),
      'POST /mcp/servers': () => listing([NOTES]),
    })
    render(<ToolServerSettings />)
    await userEvent.click(await screen.findByRole('button', { name: 'Add a tool server' }))
    await userEvent.type(screen.getByLabelText('Name'), 'My Notes')
    await userEvent.type(screen.getByLabelText('Program'), 'C:\\tools\\notes.exe')
    await userEvent.type(screen.getByLabelText('Variables'), 'NOTES_KEY=sk-proj-FAKE')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await screen.findByRole('article', { name: 'Notes' })
    const [, post] = backend.mock.calls.find(([, init]) => init?.method === 'POST') ?? []
    expect(JSON.parse(String(post?.body))).toMatchObject({
      id: 'my-notes',
      name: 'My Notes',
      command: 'C:\\tools\\notes.exe',
      env: { NOTES_KEY: 'sk-proj-FAKE' },
    })
  })

  it('shows the refusal in words when a setup cannot be used', async () => {
    serve({
      'GET /mcp': () => listing([]),
      'POST /mcp/servers': () => json({ code: 'setup_invalid', detail: "There's no program at that path." }, 422),
    })
    render(<ToolServerSettings />)
    await userEvent.click(await screen.findByRole('button', { name: 'Add a tool server' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Notes')
    await userEvent.type(screen.getByLabelText('Program'), 'C:\\nothing.exe')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText("There's no program at that path.")).toBeTruthy()
  })

  it('keeps the list but says so when reading it again fails', async () => {
    let up = true
    serve({ 'GET /mcp': () => (up ? listing([NOTES]) : json({ detail: 'Not available' }, 503)) })
    render(<ToolServerSettings />)
    await screen.findByRole('article', { name: 'Notes' })
    up = false
    act(() => lane.handlers.onToolServersChanged?.())
    expect(await screen.findByText(/may be out of date/)).toBeTruthy()
    expect(screen.getByRole('article', { name: 'Notes' })).toBeTruthy()
  })

  it('reads them again when they change anywhere', async () => {
    let servers: unknown[] = []
    serve({ 'GET /mcp': () => listing(servers) })
    render(<ToolServerSettings />)
    await screen.findByText('No tool servers yet')
    servers = [NOTES]
    act(() => lane.handlers.onToolServersChanged?.())
    expect(await screen.findByRole('article', { name: 'Notes' })).toBeTruthy()
  })
})

describe('the form reads', () => {
  it('a name as an id', () => {
    expect(idFor('My Notes!')).toBe('my-notes')
    expect(idFor('  ---  ')).toBe('')
  })

  it('variables one per line, a bare name keeping its saved value', () => {
    expect(readVariables('A=1\n\nB=x=y\nKEPT', ['KEPT'])).toEqual({ A: '1', B: 'x=y', KEPT: null })
    expect(readVariables('A=1\nNEW', [])).toBe(2) // a bare name with nothing saved
    expect(readVariables('1BAD=x', [])).toBe(1)
  })
})
