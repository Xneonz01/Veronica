// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage, LaneState } from '../../chat/types'
import { stubTopLayer } from '../../ui/__tests__/topLayer'
import { Answer } from '../Answer'
import { chatProblem } from '../chatProblems'
import { Composer } from '../Composer'
import { MemoryLine } from '../MemoryLine'
import { RichText } from '../RichText'
import { Transcript } from '../Transcript'
import type { MemoryAction, Notification } from '../../notify/types'

beforeEach(() => {
  stubTopLayer()
})
afterEach(cleanup)

function message(id: number, role: 'user' | 'assistant', content: string, extra: Partial<ChatMessage> = {}): ChatMessage {
  return { id, role, content, citations: [], tools: [], ...extra }
}

const cited = message(2, 'assistant', 'The first can start on the 30th [1].', {
  tools: [{ name: 'files_search', result: 'Found 3 files: roof-repair.md', ok: true, code: '' }],
  citations: [{ n: 1, title: 'roof-repair.md', ordinal: 0, text: 'Ash & Sons can start on the 30th.' }],
})

describe('Composer', () => {
  function Harness({ busy = false, lane = 'open' as LaneState, onSend = vi.fn(), onStop = vi.fn(), onAttach = vi.fn() }) {
    const [value, setValue] = useState('')
    const field = useRef<HTMLTextAreaElement | null>(null)
    return (
      <Composer
        value={value}
        onChange={setValue}
        onSend={onSend}
        onStop={onStop}
        busy={busy}
        lane={lane}
        attachments={[]}
        uploading={[]}
        onAttach={onAttach}
        onRemoveAttachment={() => undefined}
        fieldRef={field}
      />
    )
  }

  it('sends on Enter and starts a new line on Shift+Enter', async () => {
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)
    const field = screen.getByRole('textbox', { name: 'Message Veronica' })
    await userEvent.type(field, 'first line{Shift>}{Enter}{/Shift}second')
    expect(onSend).not.toHaveBeenCalled()
    expect((field as HTMLTextAreaElement).value).toBe('first line\nsecond')
    await userEvent.type(field, '{Enter}')
    expect(onSend).toHaveBeenCalledOnce()
  })

  it("won't send nothing, and Send says so by being unavailable", async () => {
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', true)
    await userEvent.type(screen.getByRole('textbox'), '   {Enter}')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('leaves Enter to an input method that is composing a character', () => {
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)
    const field = screen.getByRole('textbox')
    fireEvent.change(field, { target: { value: 'にほん' } })
    fireEvent.keyDown(field, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
  })

  it('turns Send into Stop while she answers, and Escape stops too', async () => {
    const onStop = vi.fn()
    render(<Harness busy onStop={onStop} />)
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Stop answering' }))
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    expect(onStop).toHaveBeenCalledTimes(2)
  })

  it('says whether it is connecting for the first time, reconnecting, or paused', () => {
    const { rerender } = render(<Harness lane="connecting" />)
    expect(screen.getByRole('textbox').getAttribute('placeholder')).toBe('Connecting to Veronica…')
    rerender(<Harness lane="offline" />)
    expect(screen.getByRole('textbox').getAttribute('placeholder')).toBe('Reconnecting…')
    rerender(<Harness lane="unavailable" />)
    expect(screen.getByRole('textbox').getAttribute('placeholder')).toBe('Chat is paused')
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: 'Attach files' })).toHaveProperty('disabled', true)
  })

  it('keeps taking words while the connection is down, and sends only once it is back', async () => {
    const onSend = vi.fn()
    const { rerender } = render(<Harness lane="offline" onSend={onSend} />)
    const field = screen.getByRole('textbox')
    expect(field).toHaveProperty('disabled', false)
    await userEvent.type(field, 'Still writing{Enter}')
    expect((field as HTMLTextAreaElement).value).toBe('Still writing')
    expect(screen.getByRole('button', { name: 'Send' })).toHaveProperty('disabled', true)
    expect(onSend).not.toHaveBeenCalled()
    rerender(<Harness lane="open" onSend={onSend} />)
    await userEvent.type(field, '{Enter}')
    expect(onSend).toHaveBeenCalledOnce()
  })

  it('names a pasted image so its chip is not blank', () => {
    const onAttach = vi.fn()
    render(<Harness onAttach={onAttach} />)
    const image = new File(['x'], '', { type: 'image/png' })
    fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { files: [image] } })
    expect(onAttach.mock.calls[0][0][0].name).toBe('pasted-image.png')
  })
})

describe('Answer', () => {
  it('shows what she did, the answer, where it came from, and a way to copy it', async () => {
    const onCite = vi.fn()
    render(<Answer message={cited} live={false} state="idle" onCite={onCite} />)
    expect(screen.getByText('Found 3 files: roof-repair.md')).toBeTruthy()
    expect(screen.getByText('From your files')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Copy answer' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Source 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Source 1: roof-repair.md' }))
    expect(onCite).toHaveBeenCalledTimes(2)
    expect(onCite).toHaveBeenCalledWith(cited.citations[0])
  })

  it('shows what she said she did and did not, under the answer, once it has settled', () => {
    const claimed = message(5, 'assistant', "I've forgotten that.", {
      caveats: [{ family: 'memory', text: "My memory didn't actually change just now. Ask me again if you want it to." }],
    })
    const { rerender } = render(<Answer message={{ ...claimed, streaming: true }} live state="streaming" onCite={() => undefined} />)
    expect(screen.queryByText("My memory didn't actually change just now. Ask me again if you want it to.")).toBeNull()
    rerender(<Answer message={claimed} live={false} state="idle" onCite={() => undefined} />)
    expect(screen.getByText("My memory didn't actually change just now. Ask me again if you want it to.")).toBeTruthy()
  })

  it('has nothing to open when the line says it all', () => {
    const { container } = render(<Answer message={cited} live={false} state="idle" onCite={() => undefined} />)
    expect(container.querySelector('details')).toBeNull()
  })

  it('opens a tool line to show the rest of what came back', async () => {
    const recalled = message(4, 'assistant', 'You like tea.', {
      tools: [{ name: 'memory_recall', result: 'Recall — Prefers tea.\nRecall — Walks on Sundays.', ok: true, code: '' }],
    })
    const { container } = render(<Answer message={recalled} live={false} state="idle" onCite={() => undefined} />)
    const card = container.querySelector('details')
    expect(card?.open).toBe(false)
    await userEvent.click(screen.getByText('Prefers tea.'))
    expect(card?.open).toBe(true)
    expect(card?.textContent).toContain('Walks on Sundays.')
  })

  it('says she is thinking before her first word, and nothing of sources until she is done', () => {
    render(<Answer message={null} live state="thinking" onCite={() => undefined} />)
    expect(screen.getByRole('status').textContent).toBe('Thinking it through')
    expect(screen.queryByText('Sources')).toBeNull()
  })

  it('says an answer came from the web whenever a web tool ran, ahead of anything else', () => {
    // (mutation: no web label; files first)
    const browsed = message(6, 'assistant', 'Trams run every 12 minutes [1].', {
      citations: cited.citations,
      tools: [
        { name: 'files_search', result: 'Found 1 file', ok: true, code: '' },
        { name: 'web_read', result: 'Read a page on example.org.', ok: true, code: '' },
      ],
    })
    const failed = message(7, 'assistant', 'I could not search.', {
      tools: [{ name: 'web_search', result: 'The search service turned that down.', ok: false, code: 'search_unavailable' }],
    })
    const { unmount } = render(<Answer message={browsed} live={false} state="idle" onCite={() => undefined} />)
    expect(screen.getByText('From the web')).toBeTruthy()
    expect(screen.queryByText('From your files')).toBeNull()
    unmount()
    render(<Answer message={failed} live={false} state="idle" onCite={() => undefined} />)
    expect(screen.queryByText('From the web')).toBeNull()
  })

  it('names a failed action by what she could not do', () => {
    const failed = message(3, 'assistant', 'I could not look.', {
      tools: [{ name: 'files_list', result: "I couldn't open that folder.", ok: false, code: 'not_found' }],
    })
    render(<Answer message={failed} live={false} state="idle" onCite={() => undefined} />)
    expect(screen.getByText(/Couldn't look in the folder/)).toBeTruthy()
    expect(screen.getByText("I couldn't open that folder.")).toBeTruthy()
  })
})

describe('Transcript', () => {
  function renderTranscript(props: Partial<Parameters<typeof Transcript>[0]> = {}) {
    Element.prototype.scrollIntoView = vi.fn()
    return render(
      <Transcript
        messages={[message(1, 'user', 'What did I note about the roof?'), cited]}
        state="idle"
        conversationId={1}
        stoppedAfter={null}
        problem={null}
        onRetry={() => undefined}
        onDismissProblem={() => undefined}
        receipts={[]}
        onUndoReceipt={() => undefined}
        {...props}
      />,
    )
  }

  it('sets each question as the heading of its answer', () => {
    renderTranscript()
    expect(screen.getByRole('heading', { level: 2, name: 'What did I note about the roof?' })).toBeTruthy()
  })

  it('receipts every memory write of the last answer, each on its own line', () => {
    const note = (id: string, summary: string): Notification => ({
      id,
      action: 'removed',
      summary,
      source: 'user',
      kind: 'fact',
      memoryId: 1,
      undoable: false,
      confidence: 1,
      createdAt: 1,
      members: [],
    })
    renderTranscript({ receipts: [note('n1', 'User collects stamps'), note('n2', 'User collects coins')] })
    expect(screen.getByText('User collects stamps')).toBeTruthy()
    expect(screen.getByText('User collects coins')).toBeTruthy()
  })

  it('says when you stopped an answer', () => {
    renderTranscript({ messages: [message(5, 'user', 'Gift ideas?')], stoppedAfter: 5 })
    expect(screen.getByText('You stopped this answer before it finished.')).toBeTruthy()
  })

  it('shows a failure in her words, with a way to ask again', async () => {
    const onRetry = vi.fn()
    renderTranscript({ messages: [message(5, 'user', 'Gift ideas?')], problem: chatProblem('llm_unreachable'), onRetry })
    const notice = screen.getByRole('alert') // read out the moment it appears
    expect(within(notice).getByText("I can't think right now")).toBeTruthy()
    await userEvent.click(within(notice).getByRole('button', { name: 'Ask again' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('still shows a failure when there are no turns to hang it on', () => {
    renderTranscript({ messages: [], problem: chatProblem('bad_conversation') })
    expect(screen.getByText("That conversation isn't here any more")).toBeTruthy()
  })

  it('says a stored conversation is empty rather than showing nothing', () => {
    renderTranscript({ messages: [] })
    expect(screen.getByText('Nothing in this conversation yet. What you write next starts it.')).toBeTruthy()
  })

  it('says an answer came back empty, instead of leaving her mark alone', () => {
    renderTranscript({ messages: [message(1, 'user', 'Anything?'), message(2, 'assistant', '')] })
    expect(screen.getByText('No answer came back. Ask again if you still need one.')).toBeTruthy()
  })

  it('shows a long question whole, keeping a short heading for navigation', () => {
    const long = 'Here is the whole letter from the landlord.\nDear tenant, the boiler service is booked for Tuesday.'
    renderTranscript({ messages: [message(1, 'user', long)] })
    expect(screen.getByText(/the boiler service is booked/)).toBeTruthy()
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe(long.slice(0, 80))
  })
})

describe('RichText', () => {
  it('renders lists, tables and code as real elements', () => {
    const { container } = render(
      <RichText source={'1. one\n\n2. two\n\n| A | B |\n|---|---|\n| x | y |\n\n```js\nlet a = 1\n```'} />,
    )
    expect(container.querySelectorAll('ol > li')).toHaveLength(2)
    expect(screen.getByRole('columnheader', { name: 'A' })).toBeTruthy()
    expect(container.querySelector('pre code')?.textContent).toBe('let a = 1')
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
  })

  it('marks what renders as nothing in code and addresses, and copies code as shown', async () => {
    // (mutations: show code, inline code or an address as it came; copy the raw code)
    const writeText = vi.fn(async (_text: string) => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const { container } = render(
      <RichText source={"Run `ok\u200b()` from [the docs](https://exa\u200bmple.org/a).\n\n```js\nlet role = 'user\u202e'\n```"} />,
    )
    expect(container.querySelector('p code')?.textContent).toBe('ok⟪U+200B⟫()')
    expect(container.textContent).toContain('(exa⟪U+200B⟫mple.org/a)')
    expect(container.querySelector('pre code')?.textContent).toBe("let role = 'user⟪U+202E⟫'")
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith("let role = 'user⟪U+202E⟫'")
  })

  it('never turns model text into markup', () => {
    const { container } = render(<RichText source={'<img src=x onerror="alert(1)"> **bold**'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">')
  })
})

describe('MemoryLine', () => {
  function receipt(action: MemoryAction): Notification {
    return {
      id: 'n1',
      action,
      summary: 'User takes their coffee with milk',
      source: 'user',
      kind: 'fact',
      memoryId: 7,
      undoable: true,
      confidence: 1,
      createdAt: 1,
      members: [],
    }
  }

  it('says what she did with her memory, a correction as a correction', () => {
    const labels: [MemoryAction, string][] = [
      ['added', 'Remembered'],
      ['superseded', 'Corrected'],
      ['removed', 'Forgot'],
    ]
    for (const [action, label] of labels) {
      const { unmount } = render(<MemoryLine receipt={receipt(action)} onUndo={vi.fn()} />)
      expect(screen.getByText(label)).toBeTruthy()
      unmount()
    }
  })
})
