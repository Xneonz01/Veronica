// The overlays, working: a sheet with a form, a destructive confirmation that
// fails once and then succeeds, a menu in a popover, and a popover inside a
// sheet, where Escape must close only the top one.

import { Ellipsis, FolderInput, Pencil, Pin, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button, IconButton } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { TextField } from '../ui/Field'
import { Menu, type MenuItem } from '../ui/Menu'
import { Popover } from '../ui/Popover'
import { Sheet } from '../ui/Sheet'
import { Row, Section, Tile, Tiles } from './Specimen'

export function Overlays() {
  return (
    <Section
      id="overlays"
      title="Overlays"
      lead="One modal surface, one confirmation, one anchored panel. Focus goes in on opening and back to what opened it on closing."
    >
      <Tiles>
        <RenameSheet />
        <DeleteConfirm />
        <ConversationMenu />
        <NestedPopover />
      </Tiles>
    </Section>
  )
}

function RenameSheet() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('Roof repairs')
  const field = useRef<HTMLInputElement>(null)
  return (
    <Tile label="Sheet" note="The keyboard lands in the field">
      <Row>
        <Button onClick={() => setOpen(true)}>Rename conversation</Button>
      </Row>
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        title="Rename conversation"
        description="A name you'll recognise in the list."
        initialFocus={field}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => setOpen(false)} disabled={name.trim() === ''}>
              Save
            </Button>
          </>
        }
      >
        <TextField ref={field} label="Name" value={name} onChange={(event) => setName(event.target.value)} />
      </Sheet>
    </Tile>
  )
}

function DeleteConfirm() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [attempts, setAttempts] = useState(0)

  function confirm() {
    setBusy(true)
    setError(null)
    // The first attempt fails on purpose, to show the failure state.
    window.setTimeout(() => {
      setBusy(false)
      if (attempts === 0) {
        setAttempts(1)
        setError("I couldn't delete it just now. Try again.")
      } else {
        setAttempts(0)
        setOpen(false)
      }
    }, 1200)
  }

  return (
    <Tile label="Confirm" note="Cancel has the focus; fails once, then works">
      <Row>
        <Button variant="danger" icon={<Trash2 size={16} aria-hidden="true" />} onClick={() => setOpen(true)}>
          Delete conversation
        </Button>
      </Row>
      <Confirm
        open={open}
        title="Delete this conversation?"
        confirmLabel="Delete"
        cancelLabel="Keep it"
        destructive
        busy={busy}
        error={error}
        onConfirm={confirm}
        onCancel={() => {
          setOpen(false)
          setError(null)
        }}
      >
        “Roof repairs” and its messages go for good. What she learned from it stays in her memory.
      </Confirm>
    </Tile>
  )
}

function conversationActions(onPick: (label: string) => void): MenuItem[] {
  return [
    { id: 'rename', label: 'Rename', icon: <Pencil size={16} />, onSelect: () => onPick('Rename') },
    {
      id: 'move',
      label: 'Move to project',
      hint: 'House, Work, Reading',
      icon: <FolderInput size={16} />,
      onSelect: () => onPick('Move to project'),
    },
    { id: 'pin', label: 'Pin', icon: <Pin size={16} />, onSelect: () => onPick('Pin') },
    { id: 'delete', label: 'Delete', icon: <Trash2 size={16} />, destructive: true, onSelect: () => onPick('Delete') },
  ]
}

function ConversationMenu() {
  const anchor = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  return (
    <Tile label="Popover and menu" note={picked ? `Chose “${picked}”` : 'Arrows, Home, End, a letter, Tab'}>
      <Row>
        <IconButton
          ref={anchor}
          label="Conversation options"
          variant="secondary"
          icon={<Ellipsis size={18} aria-hidden="true" />}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((was) => !was)}
        />
      </Row>
      <Popover anchor={anchor} open={open} onClose={() => setOpen(false)} label="Conversation options">
        <Menu label="Conversation options" items={conversationActions(setPicked)} onDone={() => setOpen(false)} />
      </Popover>
    </Tile>
  )
}

function NestedPopover() {
  const [sheet, setSheet] = useState(false)
  const [menu, setMenu] = useState(false)
  const anchor = useRef<HTMLButtonElement>(null)
  return (
    <Tile label="Popover in a sheet" note="Escape closes the menu, then the sheet">
      <Row>
        <Button onClick={() => setSheet(true)}>Open a sheet with a menu</Button>
      </Row>
      <Sheet open={sheet} onClose={() => setSheet(false)} title="Project settings">
        <Row>
          <Button
            ref={anchor}
            aria-haspopup="menu"
            aria-expanded={menu}
            icon={<Ellipsis size={16} aria-hidden="true" />}
            onClick={() => setMenu((was) => !was)}
          >
            More
          </Button>
        </Row>
        <Popover anchor={anchor} open={menu} onClose={() => setMenu(false)} label="Project options">
          <Menu label="Project options" items={conversationActions(() => undefined)} onDone={() => setMenu(false)} />
        </Popover>
      </Sheet>
    </Tile>
  )
}
