// Setting up a tool server: what it's called, the program and its arguments, its
// variables, and whether it reaches the internet. A saved variable's value is never
// shown again: a line with only its name keeps it.

import { useEffect, useRef, useState } from 'react'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { TextArea, TextField } from '../ui/Field'
import { Sheet } from '../ui/Sheet'
import { Switch } from '../ui/Switch'
import styles from './ToolServerSettings.module.css'
import { ID, idFor, readVariables } from './toolServerInput'
import type { ServerSetup, ToolServer } from './useToolServers'

export function ToolServerForm({
  editing,
  onClose,
  onSave,
}: {
  editing: ToolServer | null
  onClose: () => void
  onSave: (setup: ServerSetup) => Promise<void>
}) {
  const [name, setName] = useState(editing?.name ?? '')
  const [id, setId] = useState(editing?.id ?? '')
  const [command, setCommand] = useState(editing?.command ?? '')
  const [args, setArgs] = useState(editing?.args.join('\n') ?? '')
  const [variables, setVariables] = useState(editing?.env.join('\n') ?? '')
  const [internet, setInternet] = useState(editing?.internet ?? false)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const problem = useRef<HTMLDivElement>(null)
  // The form scrolls: a refusal below the fold read as Add doing nothing.
  useEffect(() => {
    if (failed) problem.current?.scrollIntoView?.({ block: 'nearest' }) // jsdom has none
  }, [failed])

  const shownId = editing ? id : id || idFor(name)
  const env = readVariables(variables, editing?.env ?? [])
  const idProblem = shownId && !ID.test(shownId) ? 'Use lowercase letters, digits and dashes, up to 32.' : undefined
  const envProblem = typeof env === 'number' ? `Line ${env} isn't NAME=value.` : undefined
  const ready = name.trim() && shownId && command.trim() && !idProblem && !envProblem

  async function save() {
    if (!ready || typeof env === 'number') return
    setSaving(true)
    setFailed(null)
    try {
      await onSave({
        id: shownId,
        name: name.trim(),
        command: command.trim(),
        args: args.split('\n').map((line) => line.trim()).filter(Boolean),
        env,
        enabled: editing?.enabled ?? true,
        internet,
      })
      onClose()
    } catch (error) {
      setFailed(messageFor(error, "Couldn't save that. Try again in a moment."))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Sheet
      open
      onClose={onClose}
      title={editing ? `Change ${editing.name}` : 'Add a tool server'}
      description="A program on this computer that gives her more tools, over the Model Context Protocol. It runs with your account's rights, so add only one you trust."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} disabled={!ready} onClick={() => void save()}>
            {editing ? 'Save' : 'Add'}
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <TextField label="Name" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
        <TextField
          label="Id"
          hint="Its tools are named after it. Lowercase letters, digits and dashes."
          value={shownId}
          disabled={editing !== null}
          error={idProblem}
          spellCheck={false}
          onChange={(event) => setId(event.target.value)}
        />
        <TextField
          label="Program"
          hint={
            editing
              ? 'Changing the program or its arguments makes it another program: the tools you allowed go, to be looked at anew.'
              : 'The full path to an .exe, .cmd or .bat file, such as C:\\Program Files\\nodejs\\npx.cmd.'
          }
          value={command}
          spellCheck={false}
          onChange={(event) => setCommand(event.target.value)}
        />
        <TextArea
          label="Arguments"
          hint="One per line. A .cmd or .bat file takes only letters, digits and @._/:=,+\- in them."
          rows={4}
          spellCheck={false}
          value={args}
          onChange={(event) => setArgs(event.target.value)}
        />
        <TextArea
          label="Variables"
          hint="One per line, as NAME=value. Kept sealed on this computer; a saved value isn't shown again, and a line with only its name keeps it."
          rows={3}
          spellCheck={false}
          autoComplete="off"
          value={variables}
          error={envProblem}
          onChange={(event) => setVariables(event.target.value)}
        />
        <Switch
          label="Reaches the internet"
          description="Say so if it fetches or sends anything online. Each of its prompts will say it."
          checked={internet}
          onChange={setInternet}
        />
        <div ref={problem}>
          {failed && (
            <Notice tone="critical" live>
              {failed}
            </Notice>
          )}
        </div>
      </div>
    </Sheet>
  )
}
