// Test data for the shell: conversations, a workspace and notifications with spies,
// for Providers.tsx to hand to the piece under test.

import { vi } from 'vitest'
import type { ConversationSummary } from '../../chat/types'
import type { NotificationContextValue } from '../../notify/context'
import type { Notification } from '../../notify/types'
import type { Workspace } from '../../workspace/context'

export function conversation(id: number, title: string, projectId: number | null = null): ConversationSummary {
  const now = new Date().toISOString()
  return { id, title, created_at: now, updated_at: now, message_count: 2, project_id: projectId }
}

export function fakeWorkspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    conversations: [],
    conversationsLoading: false,
    conversationsError: null,
    projects: [],
    projectsLoading: false,
    projectsError: null,
    reloadLists: vi.fn(),
    activeProjectId: null,
    openConversationId: null,
    newChat: vi.fn(),
    openConversation: vi.fn(),
    selectProject: vi.fn(),
    createProject: vi.fn(async () => null),
    removeProject: vi.fn(async () => true),
    renameConversation: vi.fn(async () => true),
    removeConversation: vi.fn(async () => true),
    moveConversation: vi.fn(async () => true),
    ask: vi.fn(() => true),
    chatUnavailable: false,
    ...overrides,
  }
}

export function fakeNotifications(overrides: Partial<NotificationContextValue> = {}): NotificationContextValue {
  return {
    toasts: [],
    history: [],
    latest: null,
    unseen: 0,
    settings: { verbosity: 'subtle', sounds: false, setVerbosity: vi.fn(), setSounds: vi.fn() },
    dismiss: vi.fn(),
    undo: vi.fn(),
    markSeen: vi.fn(),
    ...overrides,
  }
}

export function memoryNote(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 'n1',
    action: 'added',
    summary: 'You prefer short answers in the morning.',
    source: 'user',
    kind: 'preference',
    memoryId: 7,
    undoable: true,
    confidence: 1,
    createdAt: 1,
    members: [],
    ...overrides,
  }
}
