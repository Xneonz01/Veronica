// The workspace: the conversation on screen, the list of conversations, and the
// projects that file them. It lives in the shell, not in the chat page, so the
// sidebar can show it on every page and a reply keeps streaming while you look at
// something else.
//
// Two contexts on purpose. The chat session changes on every streamed token; the
// workspace changes when a turn ends. The sidebar reads only the second, so a long
// reply never re-renders the conversation list.

import { createContext, useContext } from 'react'
import type { ConversationSummary, UseChat } from '../chat/types'
import type { Project } from '../chat/useProjects'

export interface Workspace {
  conversations: ConversationSummary[]
  conversationsLoading: boolean
  conversationsError: string | null
  projects: Project[]
  projectsLoading: boolean
  projectsError: string | null
  /** Load both lists again, after a failure. */
  reloadLists: () => void
  activeProjectId: number | null
  openConversationId: number | null
  newChat: () => void
  openConversation: (id: number) => void
  /** Also starts a fresh conversation in it, so the next turn is correctly scoped. */
  selectProject: (id: number | null) => void
  /** Opens it unless `open` is false (named, then clicked away from: the click was
      meant for something else, and must not be replaced by the new project). */
  createProject: (name: string, open?: boolean) => Promise<Project | null>
  removeProject: (id: number) => Promise<boolean>
  /** False when the old name had to be put back. */
  renameConversation: (id: number, title: string) => Promise<boolean>
  removeConversation: (id: number) => Promise<boolean>
  moveConversation: (id: number, projectId: number | null) => Promise<boolean>
  /** A new conversation that opens with this message. False if it couldn't be sent. */
  ask: (text: string) => boolean
  /** The backend refuses chat: it started without her memory, where conversations live. */
  chatUnavailable: boolean
}

export const WorkspaceContext = createContext<Workspace | null>(null)
export const ChatSessionContext = createContext<UseChat | null>(null)

export function useWorkspace(): Workspace {
  const workspace = useContext(WorkspaceContext)
  if (!workspace) throw new Error('useWorkspace needs the WorkspaceProvider (in the app shell)')
  return workspace
}

export function useChatSession(): UseChat {
  const session = useContext(ChatSessionContext)
  if (!session) throw new Error('useChatSession needs the WorkspaceProvider (in the app shell)')
  return session
}
