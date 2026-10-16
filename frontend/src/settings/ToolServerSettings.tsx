// Tool servers (ADR 0003): programs on this computer that give her more tools over the
// Model Context Protocol. Each tool is offered only once you allow it, she asks before
// every use, and none is used on a call.

import { Plug } from 'lucide-react'
import { useState } from 'react'
import { messageFor } from '../lib/api'
import { Button } from '../ui/Button'
import { Confirm } from '../ui/Confirm'
import { EmptyState, Notice, Skeleton } from '../ui/Feedback'
import { Group } from './Group'
import { ToolServerCard } from './ToolServerCard'
import { ToolServerForm } from './ToolServerForm'
import styles from './ToolServerSettings.module.css'
import { useToolServers, type ServerSetup, type ToolServer } from './useToolServers'

const LEAD =
  'Programs on this computer that give her more tools. She asks before she uses any of them, every time, and never on a call. After one answers, she asks before she saves, writes or opens anything.'

function setupOf(server: ToolServer, changes: Partial<ServerSetup> = {}): ServerSetup {
  return {
    id: server.id,
    name: server.name,
    command: server.command,
    args: server.args,
    env: Object.fromEntries(server.env.map((name) => [name, null])), // keep each saved value
    enabled: server.enabled,
    internet: server.internet,
    ...changes,
  }
}

export function ToolServerSettings() {
  const tools = useToolServers()
  const [form, setForm] = useState<{ editing: ToolServer | null } | null>(null)
  const [removing, setRemoving] = useState<ToolServer | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (tools.servers === null) {
    return (
      <Group title="Tool servers" lead={LEAD} bare={tools.loadError !== null}>
        {tools.loadError ? (
          <Notice tone="critical" live action={<Button size="sm" onClick={() => void tools.retry()}>Try again</Button>}>
            {tools.loadError}
          </Notice>
        ) : (
          <Skeleton height={120} />
        )}
      </Group>
    )
  }

  const add = (
    <Button size="sm" icon={<Plug size={16} aria-hidden="true" />} onClick={() => setForm({ editing: null })}>
      Add a tool server
    </Button>
  )

  return (
    <Group title="Tool servers" lead={LEAD} bare>
      {tools.loadError && (
        <Notice tone="attention" live action={<Button size="sm" onClick={() => void tools.retry()}>Try again</Button>}>
          {tools.loadError} What's shown may be out of date.
        </Notice>
      )}
      {tools.servers.length === 0 ? (
        <EmptyState
          icon={<Plug size={24} aria-hidden="true" />}
          title="No tool servers yet"
          action={add}
        >
          Add a program that speaks the Model Context Protocol, such as a notes or calendar server.
        </EmptyState>
      ) : (
        <div className={styles.list}>
          {tools.servers.map((server) => (
            <ToolServerCard
              key={server.id}
              server={server}
              onSwitch={(enabled) => tools.change(setupOf(server, { enabled }))}
              onEdit={() => setForm({ editing: server })}
              onRemove={() => {
                setRemoveError(null)
                setRemoving(server)
              }}
              onRestart={() => tools.restart(server.id)}
              onApprove={(tool, approved) => tools.approve(server.id, tool, approved)}
              readLog={() => tools.log(server.id)}
            />
          ))}
          <div>{add}</div>
          <p className={styles.toolDescription}>
            Up to {tools.limits.perTurn} allowed tools are offered at once, and only as many as fit in{' '}
            {tools.limits.tokens.toLocaleString()} tokens of her chat window between them: each is read by her on every
            turn of chat. One with long descriptions can take that much alone.
          </p>
        </div>
      )}
      {form && (
        <ToolServerForm
          editing={form.editing}
          onClose={() => setForm(null)}
          onSave={form.editing ? tools.change : tools.add}
        />
      )}
      <Confirm
        open={removing !== null}
        title={`Remove ${removing?.name ?? 'this server'}?`}
        confirmLabel="Remove"
        cancelLabel="Keep it"
        destructive
        busy={busy}
        error={removeError}
        onCancel={() => setRemoving(null)}
        onConfirm={async () => {
          if (removing === null) return
          setBusy(true)
          try {
            await tools.remove(removing.id)
            setRemoving(null)
          } catch (error) {
            setRemoveError(messageFor(error, "Couldn't remove it. Try again in a moment."))
          } finally {
            setBusy(false)
          }
        }}
      >
        It stops, its tools go, and so do the ones you allowed. The program itself stays on your computer.
      </Confirm>
    </Group>
  )
}
