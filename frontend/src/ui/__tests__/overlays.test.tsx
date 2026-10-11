// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Confirm } from '../Confirm'
import { Menu, type MenuItem } from '../Menu'
import { Popover } from '../Popover'
import { Sheet } from '../Sheet'
import { stubTopLayer } from './topLayer'

let topLayer: ReturnType<typeof stubTopLayer>
beforeEach(() => {
  topLayer = stubTopLayer()
})
afterEach(cleanup)

function dialogNamed(name: string): HTMLDialogElement {
  const dialog = screen.getByRole('dialog', { name })
  if (!(dialog instanceof HTMLDialogElement)) throw new Error('not a <dialog>')
  return dialog
}

describe('Sheet', () => {
  it('opens as a modal named by its title and closes when the owner says so', () => {
    const { rerender } = render(
      <Sheet open onClose={() => undefined} title="Rename" description="A clearer name">
        body
      </Sheet>,
    )
    const dialog = dialogNamed('Rename')
    expect(topLayer.showModal).toHaveBeenCalledOnce()
    expect(document.getElementById(dialog.getAttribute('aria-describedby') ?? '')?.textContent).toBe(
      'A clearer name',
    )
    rerender(
      <Sheet open={false} onClose={() => undefined} title="Rename">
        body
      </Sheet>,
    )
    expect(dialog.open).toBe(false)
  })

  it('hands Escape to the owner instead of closing itself', () => {
    const close = vi.fn()
    render(
      <Sheet open onClose={close} title="Rename">
        body
      </Sheet>,
    )
    const cancel = new Event('cancel', { cancelable: true })
    fireEvent(dialogNamed('Rename'), cancel)
    expect(close).toHaveBeenCalledOnce()
    expect(cancel.defaultPrevented).toBe(true)
    expect(dialogNamed('Rename').open).toBe(true)
  })

  it('closes from its close button and from a press on the backdrop', async () => {
    const close = vi.fn()
    render(
      <Sheet open onClose={close} title="Rename">
        body
      </Sheet>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    const dialog = dialogNamed('Rename')
    fireEvent.pointerDown(dialog)
    fireEvent.click(dialog)
    expect(close).toHaveBeenCalledTimes(2)
  })

  it('stays open when a drag that started inside ends on the backdrop', () => {
    const close = vi.fn()
    render(
      <Sheet open onClose={close} title="Rename">
        <p>body</p>
      </Sheet>,
    )
    fireEvent.pointerDown(screen.getByText('body'))
    fireEvent.click(dialogNamed('Rename'))
    expect(close).not.toHaveBeenCalled()
  })

  it('puts the keyboard where it was asked to', () => {
    function Rename() {
      const field = useRef<HTMLInputElement>(null)
      return (
        <Sheet open onClose={() => undefined} title="Rename" initialFocus={field}>
          <input ref={field} aria-label="Name" />
        </Sheet>
      )
    }
    render(<Rename />)
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Name' }))
  })

  it('tells the owner when the browser closed it', () => {
    const close = vi.fn()
    render(
      <Sheet open onClose={close} title="Rename">
        body
      </Sheet>,
    )
    fireEvent(dialogNamed('Rename'), new Event('close'))
    expect(close).toHaveBeenCalledOnce()
  })

  it('shows it again when the browser closed it and the owner keeps it open', () => {
    // A second Escape, which a page can't refuse, closed a recovery key's sheet before
    // it was saved, and the owner still said open, so the next key was never shown
    // (mutation: only tell the owner).
    render(
      <Sheet open onClose={() => undefined} title="Save your recovery key">
        body
      </Sheet>,
    )
    const sheet = dialogNamed('Save your recovery key') as HTMLDialogElement
    act(() => sheet.close())
    expect(sheet.hasAttribute('open')).toBe(true)
  })
})

describe('Confirm', () => {
  function renderConfirm(props: Partial<Parameters<typeof Confirm>[0]> = {}) {
    const handlers = { onConfirm: vi.fn(), onCancel: vi.fn() }
    render(
      <Confirm open title="Delete this conversation?" confirmLabel="Delete" destructive {...handlers} {...props}>
        It can't be undone.
      </Confirm>,
    )
    return handlers
  }

  it('gives Cancel the focus first', () => {
    renderConfirm()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
  })

  it('confirms and cancels', async () => {
    const { onConfirm, onCancel } = renderConfirm()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it("can't be dismissed while the action runs", () => {
    const { onCancel } = renderConfirm({ busy: true })
    fireEvent(dialogNamed('Delete this conversation?'), new Event('cancel', { cancelable: true }))
    expect(onCancel).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('aria-busy')).toBe('true')
  })

  it('keeps a failure on screen with its reason', () => {
    renderConfirm({ error: "I couldn't delete it. Try again." })
    expect(screen.getByRole('alert').textContent).toBe("I couldn't delete it. Try again.")
  })
})

describe('Popover', () => {
  function Anchored({ onClose = () => undefined }: { onClose?: () => void }) {
    const anchor = useRef<HTMLButtonElement>(null)
    const [open, setOpen] = useState(false)
    return (
      <>
        <button ref={anchor} type="button" onClick={() => setOpen((was) => !was)}>
          Options
        </button>
        <button type="button">Elsewhere</button>
        <Popover
          anchor={anchor}
          open={open}
          onClose={() => {
            onClose()
            setOpen(false)
          }}
          label="Conversation options"
        >
          <button type="button">Rename</button>
        </Popover>
      </>
    )
  }

  // jsdom has no :popover-open, so its own stylesheet keeps every [popover] at
  // display: none, and a hidden element's accessible name computes as empty.
  // The panel is found by its role and label attributes instead.
  const panel = () => document.querySelector('[role="dialog"][aria-label="Conversation options"]')

  it('renders nothing until it opens, then goes to the top layer', async () => {
    render(<Anchored />)
    expect(panel()).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Options' }))
    expect(panel()).not.toBeNull()
    expect(topLayer.showPopover).toHaveBeenCalledOnce()
  })

  it('closes on Escape, keeps the key from whatever sits underneath, and returns focus', async () => {
    const underneath = vi.fn()
    document.body.addEventListener('keydown', underneath)
    render(<Anchored />)
    await userEvent.click(screen.getByRole('button', { name: 'Options' }))
    screen.getByRole('button', { name: 'Rename', hidden: true }).focus()
    await userEvent.keyboard('{Escape}')
    expect(panel()).toBeNull()
    expect(underneath).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Options' }))
    document.body.removeEventListener('keydown', underneath)
  })

  it('closes on a press outside, not on one inside or on its own anchor', async () => {
    const close = vi.fn()
    render(<Anchored onClose={close} />)
    const options = screen.getByRole('button', { name: 'Options' })
    await userEvent.click(options)
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Rename', hidden: true }))
    fireEvent.pointerDown(options)
    expect(close).not.toHaveBeenCalled()
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Elsewhere' }))
    expect(close).toHaveBeenCalledOnce()
  })

  it('toggles closed from its anchor without reopening', async () => {
    render(<Anchored />)
    const options = screen.getByRole('button', { name: 'Options' })
    await userEvent.click(options)
    await userEvent.click(options)
    expect(panel()).toBeNull()
  })
})

describe('Menu', () => {
  function renderMenu() {
    const chosen: string[] = []
    const done = vi.fn()
    const item = (id: string, label: string): MenuItem => ({ id, label, onSelect: () => chosen.push(id) })
    render(
      <Menu
        label="Conversation options"
        items={[item('rename', 'Rename'), item('move', 'Move to project'), item('pin', 'Pin'), item('delete', 'Delete')]}
        onDone={done}
      />,
    )
    return { chosen, done }
  }

  const focused = () => document.activeElement?.textContent

  it('opens with the first item under the keyboard', () => {
    renderMenu()
    expect(screen.getByRole('menu', { name: 'Conversation options' })).toBeTruthy()
    expect(focused()).toBe('Rename')
  })

  it('walks with the arrow keys, wrapping at both ends, and jumps with Home and End', async () => {
    renderMenu()
    await userEvent.keyboard('{ArrowUp}')
    expect(focused()).toBe('Delete')
    await userEvent.keyboard('{ArrowDown}')
    expect(focused()).toBe('Rename')
    await userEvent.keyboard('{End}')
    expect(focused()).toBe('Delete')
    await userEvent.keyboard('{Home}')
    expect(focused()).toBe('Rename')
  })

  it('jumps to the next item starting with a typed letter', async () => {
    renderMenu()
    await userEvent.keyboard('p')
    expect(focused()).toBe('Pin')
    await userEvent.keyboard('m')
    expect(focused()).toBe('Move to project')
  })

  it('runs the chosen item, then lets the owner close', async () => {
    const { chosen, done } = renderMenu()
    await userEvent.keyboard('{ArrowDown}{Enter}')
    expect(chosen).toEqual(['move'])
    expect(done).toHaveBeenCalledOnce()
  })

  it('leaves on Tab instead of walking the items', async () => {
    const { chosen, done } = renderMenu()
    await userEvent.tab()
    expect(done).toHaveBeenCalledOnce()
    expect(chosen).toEqual([])
  })
})
