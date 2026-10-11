// Owns the chat socket, the conversation list and the projects for the whole
// window (see context.ts for why this lives in the shell).

import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAppEvents } from '../audio/useAppEvents'
import { useChat } from '../chat/useChat'
import { useConversations } from '../chat/useConversations'
import { useProjects, type Project } from '../chat/useProjects'
import { useConversationHandoff } from '../desktop/useDesktopEvents'
import { ROUTES } from '../lib/routes'
import { ChatSessionContext, WorkspaceContext, type Workspace } from './context'

/** What a click in the sidebar or the palette changes, once the chat page is reached. */
type Intent =
  | { kind: 'new' }
  | { kind: 'open'; id: number }
  | { kind: 'project'; id: number | null }
  | { kind: 'ask'; text: string }

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const chat = useChat()
  const conversations = useConversations()
  const projects = useProjects()
  const navigate = useNavigate()
  const location = useLocation()

  const { open, send, state, conversationId, lane, reconnect } = chat
  const { refresh: refreshConversations, remove: removeStored, move: moveStored } = conversations
  const { refresh: refreshProjects, activeId, setActive, create, remove: removeStoredProject } = projects

  // Ordering, titles and counts come from the server, so the lists refresh when a
  // turn finishes rather than mirroring the socket locally.
  useEffect(() => {
    if (state !== 'idle') return
    void refreshConversations()
    void refreshProjects()
  }, [state, conversationId, refreshConversations, refreshProjects])

  const reloadLists = useCallback(() => {
    void refreshConversations()
    void refreshProjects()
  }, [refreshConversations, refreshProjects])

  // A backend that was down at start, or restarted since, gets asked again as soon
  // as the event lane reaches it, and chat stops waiting out its backoff: its paused
  // note would otherwise outlast the problem banner by up to 15 s.
  const backendReached = useCallback(() => {
    reloadLists()
    reconnect()
  }, [reloadLists, reconnect])
  useAppEvents({ onConnected: backendReached })

  // The companion hands its conversation over when it is expanded into this window.
  useConversationHandoff((id) => open(id, activeId))

  // A change to what is open rides on the move to the chat page and happens on
  // arrival. A page with unsaved changes can still refuse the move ("Keep editing"),
  // and then nothing may have changed behind its dialog.
  const go = useCallback((intent: Intent) => void navigate(ROUTES.chat, { state: { intent } }), [navigate])

  const apply = useCallback(
    (intent: Intent) => {
      switch (intent.kind) {
        case 'new':
          open(null, activeId)
          break
        case 'open':
          // Opening the open one again would cancel the answer she is still writing in
          // it. The backend adopts a conversation's own project whatever is passed
          // here, so opening one from another project's list can't widen what she searches.
          if (intent.id !== conversationId) open(intent.id, activeId)
          break
        case 'project':
          setActive(intent.id)
          open(null, intent.id)
          break
        case 'ask':
          open(null, activeId)
          send(intent.text)
          break
      }
    },
    [open, send, setActive, activeId, conversationId],
  )

  // Once per arrival: `apply` changes with what it opens, and this effect runs again
  // before the address below has replaced the one carrying the intent.
  const applied = useRef<string | null>(null)
  useEffect(() => {
    const intent = (location.state as { intent?: Intent } | null)?.intent
    if (!intent || applied.current === location.key) return
    applied.current = location.key
    apply(intent)
    // Spent: going back to this entry later must not do it again.
    void navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
  }, [location, apply, navigate])

  const newChat = useCallback(() => go({ kind: 'new' }), [go])
  const openConversation = useCallback((id: number) => go({ kind: 'open', id }), [go])
  const selectProject = useCallback((id: number | null) => go({ kind: 'project', id }), [go])

  const createProject = useCallback(
    async (name: string, openIt = true): Promise<Project | null> => {
      const created = await create(name)
      if (created && openIt) selectProject(created.id)
      return created
    },
    [create, selectProject],
  )

  const removeProject = useCallback(
    async (id: number) => {
      const removed = await removeStoredProject(id)
      if (!removed) return false
      if (id === activeId) open(null, null)
      // Its conversations are un-filed, not deleted: their project changed.
      void refreshConversations()
      return true
    },
    [removeStoredProject, activeId, open, refreshConversations],
  )

  const removeConversation = useCallback(
    async (id: number) => {
      const removed = await removeStored(id)
      // A transcript whose rows no longer exist can't stay on screen.
      if (removed && id === conversationId) open(null, activeId)
      return removed
    },
    [removeStored, conversationId, open, activeId],
  )

  const moveConversation = useCallback(
    async (id: number, projectId: number | null) => {
      const moved = await moveStored(id, projectId)
      if (moved) void refreshProjects() // the counts changed
      return moved
    },
    [moveStored, refreshProjects],
  )

  /** False when it can't be sent now (the connection is down), so the asker can say so. */
  const ask = useCallback(
    (text: string) => {
      if (lane !== 'open') return false
      go({ kind: 'ask', text })
      return true
    },
    [lane, go],
  )

  const workspace = useMemo<Workspace>(
    () => ({
      conversations: conversations.items,
      conversationsLoading: conversations.loading,
      conversationsError: conversations.error,
      projects: projects.items,
      projectsLoading: projects.loading,
      projectsError: projects.error,
      reloadLists,
      activeProjectId: activeId,
      openConversationId: conversationId,
      newChat,
      openConversation,
      selectProject,
      createProject,
      removeProject,
      renameConversation: conversations.rename,
      removeConversation,
      moveConversation,
      ask,
      chatUnavailable: lane === 'unavailable',
    }),
    [
      conversations.items,
      conversations.loading,
      conversations.error,
      conversations.rename,
      projects.items,
      projects.loading,
      projects.error,
      reloadLists,
      activeId,
      conversationId,
      newChat,
      openConversation,
      selectProject,
      createProject,
      removeProject,
      removeConversation,
      moveConversation,
      ask,
      lane,
    ],
  )

  return (
    <WorkspaceContext.Provider value={workspace}>
      <ChatSessionContext.Provider value={chat}>{children}</ChatSessionContext.Provider>
    </WorkspaceContext.Provider>
  )
}
