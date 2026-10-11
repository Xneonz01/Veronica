// Every control in every state it can be in: the button voices and sizes, the
// busy state, the fields with a hint and with an error, switches, segmented.

import { ArrowUp, AudioLines, Mic, MicOff, Monitor, Moon, Paperclip, Plus, Sun, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, IconButton, type ButtonVariant } from '../ui/Button'
import { SearchField, SelectField, TextArea, TextField } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { Switch } from '../ui/Switch'
import { Row, Section, Stack, Tile, Tiles } from './Specimen'

const VOICES: { variant: ButtonVariant; label: string; note: string }[] = [
  { variant: 'primary', label: 'Start a call', note: 'Veronica acting' },
  { variant: 'secondary', label: 'Add a document', note: 'A real surface, no colour' },
  { variant: 'ghost', label: 'Show more', note: 'Nothing until approached' },
  { variant: 'quiet', label: 'Open the source', note: 'A link that is a button' },
  { variant: 'danger', label: 'Forget this', note: 'Removal, never decoration' },
]

const VOICE_OPTIONS = [
  { value: 'rowan', label: 'Rowan' },
  { value: 'morgan', label: 'Morgan' },
  { value: 'sage', label: 'Sage' },
]

export function Controls() {
  return (
    <Section id="controls" title="Controls" lead="One shape, five voices. The primary action is obvious because everything around it is restrained.">
      <Tiles>
        {VOICES.map(({ variant, label, note }) => (
          <Tile key={variant} label={variant} note={note}>
            <Row>
              <Button variant={variant} size="sm">
                {label}
              </Button>
              <Button variant={variant}>{label}</Button>
            </Row>
          </Tile>
        ))}
        <Tile label="Sizes and icons" note="sm 30 · md 36 · lg 44">
          <Row>
            <Button variant="primary" size="lg" icon={<Plus size={18} aria-hidden="true" />}>
              New conversation
            </Button>
            <Button icon={<Paperclip size={16} aria-hidden="true" />}>Attach</Button>
          </Row>
        </Tile>
        <BusyTile />
        <IconButtons />
      </Tiles>
      <Fields />
      <Choices />
    </Section>
  )
}

function BusyTile() {
  const [busy, setBusy] = useState(false)
  return (
    <Tile label="Busy" note="Spinner in place of the icon; no second click">
      <Row>
        <Button
          variant="primary"
          loading={busy}
          icon={<AudioLines size={16} aria-hidden="true" />}
          onClick={() => {
            setBusy(true)
            window.setTimeout(() => setBusy(false), 1600)
          }}
        >
          Clone voice
        </Button>
        <Button variant="danger" loading icon={<Trash2 size={16} aria-hidden="true" />}>
          Deleting
        </Button>
      </Row>
    </Tile>
  )
}

function IconButtons() {
  const [muted, setMuted] = useState(false)
  return (
    <Tile label="Icon buttons" note="Named by a label; toggles report their state">
      <Row>
        <IconButton label="Attach files" icon={<Paperclip size={18} aria-hidden="true" />} />
        <IconButton
          label={muted ? 'Unmute' : 'Mute'}
          pressed={muted}
          onClick={() => setMuted((was) => !was)}
          icon={muted ? <MicOff size={18} aria-hidden="true" /> : <Mic size={18} aria-hidden="true" />}
        />
        <IconButton label="Send" variant="primary" round icon={<ArrowUp size={18} aria-hidden="true" />} />
        <IconButton label="Delete" variant="danger" icon={<Trash2 size={18} aria-hidden="true" />} />
      </Row>
    </Tile>
  )
}

function Fields() {
  const [name, setName] = useState('')
  const [voice, setVoice] = useState('rowan')
  return (
    <Tiles wide>
      <Tile label="Text" note="A label above, a hint below, an error as a sentence">
        <Stack>
          <TextField
            label="Voice name"
            placeholder="Rowan"
            hint="How she'll list this voice."
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <TextField label="Hotkey" defaultValue="Ctrl+Shift+Space" error="Another app already uses this shortcut." />
          <TextField label="Model folder" defaultValue="Managed by Veronica" disabled />
        </Stack>
      </Tile>
      <Tile label="Longer text, choices, search">
        <Stack>
          <TextArea label="How she should talk to you" defaultValue="Short answers. Ask before doing anything I can't undo." />
          <SelectField label="Voice" options={VOICE_OPTIONS} value={voice} onChange={(event) => setVoice(event.target.value)} />
          <SearchField label="Search conversations" placeholder="Search conversations" />
        </Stack>
      </Tile>
    </Tiles>
  )
}

function Choices() {
  const [wake, setWake] = useState(true)
  const [sounds, setSounds] = useState(false)
  const [theme, setTheme] = useState<'system' | 'light' | 'dark'>('system')
  const [length, setLength] = useState<'short' | 'balanced' | 'detailed'>('balanced')
  return (
    <Tiles wide>
      <Tile label="Switches" note="For a setting that applies the moment it flips">
        <Stack>
          <Switch label="Wake word" description="Listen for “Veronica” while the app is open." checked={wake} onChange={setWake} />
          <Switch label="Notification sounds" checked={sounds} onChange={setSounds} />
          <Switch label="Use the GPU for speech" description="Managed by the model manager." checked disabled onChange={() => undefined} />
        </Stack>
      </Tile>
      <Tile label="Segmented" note="One choice among a few, all visible; arrows move it">
        <Stack>
          <Segmented
            label="Theme"
            value={theme}
            onChange={setTheme}
            options={[
              { value: 'system', label: 'System', icon: <Monitor size={15} aria-hidden="true" /> },
              { value: 'light', label: 'Light', icon: <Sun size={15} aria-hidden="true" /> },
              { value: 'dark', label: 'Dark', icon: <Moon size={15} aria-hidden="true" /> },
            ]}
          />
          <Segmented
            label="Answer length"
            size="sm"
            value={length}
            onChange={setLength}
            options={[
              { value: 'short', label: 'Short' },
              { value: 'balanced', label: 'Balanced' },
              { value: 'detailed', label: 'Detailed' },
            ]}
          />
        </Stack>
      </Tile>
    </Tiles>
  )
}
