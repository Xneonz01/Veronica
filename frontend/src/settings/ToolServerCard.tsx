// One tool server: whether it is running and, if not, why; the program exactly as it
// is started, never cut short; its tools, each offered only once you allow it; and
// what it wrote to its error stream, on request.

import { useState } from 'react'
import { messageFor } from '../lib/api'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Feedback'
import { Switch } from '../ui/Switch'
import styles from './ToolServerSettings.module.css'
import type { ServerState, ServerTool, ToolServer } from './useToolServers'

/** The program and its arguments as one line, any part with a space in it quoted, so
    where each one ends can be read. */
function commandLine(server: ToolServer): string {
  return [server.command, ...server.args].map((part) => (/\s/.test(part) ? `"${part}"` : part)).join(' ')
}

const STATE: Record<ServerState, { label: string; tone: BadgeTone }> = {
  running: { label: 'Running', tone: 'success' },
  starting: { label: 'Starting', tone: 'info' },
  off: { label: 'Off', tone: 'neutral' },
  failed: { label: 'Not running', tone: 'danger' },
}

export function ToolServerCard({
  server,
  onSwitch,
  onEdit,
  onRemove,
  onRestart,
  onApprove,
  readLog,
}: {
  server: ToolServer
  onSwitch: (on: boolean) => Promise<void>
  onEdit: () => void
  onRemove: () => void
  onRestart: () => Promise<void>
  onApprove: (tool: ServerTool, approved: boolean) => Promise<void>
  readLog: () => Promise<string>
}) {
  const [failed, setFailed] = useState<string | null>(null)
  const [log, setLog] = useState<string | null>(null)
  const state = STATE[server.state]

  async function apply(task: () => Promise<void>) {
    setFailed(null)
    try {
      await task()
    } catch (error) {
      setFailed(messageFor(error, "Couldn't save that. Try again in a moment."))
    }
  }

  return (
    <article className={styles.server} aria-label={server.name}>
      <div className={styles.head}>
        <div className={styles.titles}>
          <h3 className={styles.name}>{server.name}</h3>
          <Badge tone={state.tone} size="sm">
            {state.label}
          </Badge>
          {server.internet && (
            <Badge tone="warning" size="sm">
              Reaches the internet
            </Badge>
          )}
        </div>
        <Switch label={`Run ${server.name}`} checked={server.enabled} onChange={(on) => void apply(() => onSwitch(on))} />
      </div>
      {server.detail && (
        <Notice tone={server.state === 'failed' ? 'attention' : 'info'} live>
          {server.detail}
        </Notice>
      )}
      <dl className={styles.facts}>
        <dt>Starts</dt>
        <dd className={styles.command}>{commandLine(server)}</dd>
        {server.env.length > 0 && (
          <>
            <dt>Variables</dt>
            <dd>{server.env.join(', ')}</dd>
          </>
        )}
        {server.version && (
          <>
            <dt>Speaks</dt>
            <dd>
              MCP {server.version}
              {server.serverName && ` as “${server.serverName}”`}
            </dd>
          </>
        )}
      </dl>
      {server.tools.length > 0 && (
        <ul className={styles.tools} aria-label={`${server.name}'s tools`}>
          {server.tools.map((tool) => (
            <ToolLine key={tool.name} tool={tool} onApprove={(approved) => apply(() => onApprove(tool, approved))} />
          ))}
        </ul>
      )}
      <div className={styles.actions}>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          Change
        </Button>
        <Button size="sm" variant="ghost" disabled={!server.enabled} onClick={() => void apply(onRestart)}>
          Restart
        </Button>
        <Button size="sm" variant="ghost" onClick={() => void readLog().then(setLog)}>
          {log === null ? 'Show its log' : 'Read its log again'}
        </Button>
        <Button size="sm" variant="danger" onClick={onRemove}>
          Remove
        </Button>
      </div>
      {log !== null && (
        <pre className={styles.log} aria-label={`What ${server.name} wrote to its log`}>
          {log}
        </pre>
      )}
      {failed && (
        <Notice tone="critical" live>
          {failed}
        </Notice>
      )}
    </article>
  )
}

function ToolLine({ tool, onApprove }: { tool: ServerTool; onApprove: (approved: boolean) => Promise<void> }) {
  return (
    <li className={styles.tool}>
      <div className={styles.toolText}>
        <span className={styles.toolName}>{tool.title}</span>
        {tool.description && <span className={styles.toolDescription}>{tool.description}</span>}
        {tool.arguments.length > 0 && (
          <span className={styles.toolDescription}>Takes: {tool.arguments.join(', ')}</span>
        )}
        {tool.state === 'changed' && <span className={styles.toolNote}>Changed since you allowed it: read it again.</span>}
        {tool.state === 'unsupported' && <span className={styles.toolNote}>Can't be offered: {tool.unsupported}.</span>}
        {tool.state === 'approved' && !tool.offered && (
          <span className={styles.toolNote}>Allowed, but waiting: there's no room for it beside the tools already offered.</span>
        )}
      </div>
      {tool.state === 'approved' ? (
        <Button size="sm" variant="ghost" aria-label={`Withdraw ${tool.title}`} onClick={() => void onApprove(false)}>
          Withdraw
        </Button>
      ) : tool.state !== 'unsupported' ? (
        <Button size="sm" aria-label={`Allow ${tool.title}`} onClick={() => void onApprove(true)}>
          {tool.state === 'changed' ? 'Allow again' : 'Allow'}
        </Button>
      ) : null}
    </li>
  )
}
