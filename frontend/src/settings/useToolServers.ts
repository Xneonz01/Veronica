// Tool servers as the backend holds them (ADR 0003): loaded when Settings opens, reloaded
// when the event lane reconnects, and followed live as servers start, stop and change.
// A server's variables go in and never come back: only their names do.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { ApiError, apiFetch, failureMessage, messageFor } from '../lib/api'

export type ServerState = 'off' | 'starting' | 'running' | 'failed'
export type ToolState = 'new' | 'changed' | 'approved' | 'unsupported'

export interface ServerTool {
  name: string
  title: string
  description: string
  state: ToolState
  unsupported: string
  offered: boolean
  arguments: string[]
  pin: string // what it is approved under, sent back so the tool approved is the one shown
}

export interface ToolServer {
  id: string
  name: string
  command: string
  args: string[]
  env: string[] // the names of its variables; their values never come back
  enabled: boolean
  internet: boolean
  state: ServerState
  detail: string
  version: string | null
  serverName: string
  tools: ServerTool[]
}

/** A setup as the form sends it: a variable whose value is null keeps its saved one. */
export interface ServerSetup {
  id: string
  name: string
  command: string
  args: string[]
  env: Record<string, string | null>
  enabled: boolean
  internet: boolean
}

interface Listing {
  servers: (Omit<ToolServer, 'serverName'> & { server_name: string })[]
  per_turn: number
  tokens: number
}

const LOAD_FAILED = "Couldn't load the tool servers. Try again in a moment."
const SAVE_FAILED = "Couldn't save that. Try again in a moment."

function toServers(body: Listing): ToolServer[] {
  return body.servers.map(({ server_name, ...server }) => ({ ...server, serverName: server_name }))
}

export function useToolServers() {
  const [servers, setServers] = useState<ToolServer[] | null>(null)
  const [limits, setLimits] = useState({ perTurn: 12, tokens: 1500 })
  const [loadError, setLoadError] = useState<string | null>(null)
  const latest = useRef(0) // only the newest load or change may write, as in useWebSettings

  const take = useCallback((body: Listing, mine: number) => {
    if (mine !== latest.current) return
    setServers(toServers(body))
    setLimits({ perTurn: body.per_turn, tokens: body.tokens })
  }, [])

  const load = useCallback(async () => {
    const mine = ++latest.current
    setLoadError(null)
    try {
      const response = await apiFetch('/mcp')
      if (!response.ok) {
        const message = await failureMessage(response, LOAD_FAILED)
        if (mine === latest.current) setLoadError(message)
        return
      }
      take((await response.json()) as Listing, mine)
    } catch (error) {
      if (mine === latest.current) setLoadError(messageFor(error, LOAD_FAILED))
    }
  }, [take])

  useEffect(() => {
    void load()
  }, [load])

  useAppEvents({ onToolServersChanged: () => void load(), onConnected: () => void load() })

  /** Sends one change; throws an ApiError whose message is safe to show. */
  const send = useCallback(
    async (method: string, path: string, body?: unknown): Promise<void> => {
      const mine = ++latest.current
      const response = await apiFetch(path, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!response.ok) throw new ApiError('rejected', await failureMessage(response, SAVE_FAILED))
      take((await response.json()) as Listing, mine)
    },
    [take],
  )

  const add = useCallback((setup: ServerSetup) => send('POST', '/mcp/servers', setup), [send])
  const change = useCallback(
    (setup: ServerSetup) => send('PUT', `/mcp/servers/${encodeURIComponent(setup.id)}`, setup),
    [send],
  )
  const remove = useCallback((id: string) => send('DELETE', `/mcp/servers/${encodeURIComponent(id)}`), [send])
  const restart = useCallback((id: string) => send('POST', `/mcp/servers/${encodeURIComponent(id)}/restart`), [send])
  const approve = useCallback(
    (id: string, tool: ServerTool, approved: boolean) =>
      send('PUT', `/mcp/servers/${encodeURIComponent(id)}/tools/${encodeURIComponent(tool.name)}`, {
        approved,
        pin: tool.pin,
      }),
    [send],
  )

  /** What the server wrote to its error stream, or a sentence saying it couldn't be read. */
  const log = useCallback(async (id: string): Promise<string> => {
    try {
      const response = await apiFetch(`/mcp/servers/${encodeURIComponent(id)}/log`)
      if (!response.ok) return await failureMessage(response, "Couldn't read its log.")
      const body = (await response.json()) as { log: string }
      return body.log || 'It hasn’t written anything.'
    } catch (error) {
      return messageFor(error, "Couldn't read its log.")
    }
  }, [])

  return { servers, limits, loadError, retry: load, add, change, remove, restart, approve, log }
}
