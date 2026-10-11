// Everything the command palette offers for a query: ask her, go somewhere, act,
// open a project or a conversation. Built fresh per query from the live workspace.

import {
  AudioLines,
  AudioWaveform,
  Brain,
  Folder,
  History,
  Info,
  Keyboard,
  MessageSquare,
  Mic,
  MicOff,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Phone,
  PhoneOff,
  Settings,
  Sparkles,
  SquarePen,
  Sun,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { CallStatus } from '../call/context'
import { relativeTime } from '../memory/format'
import { ROUTES } from '../lib/routes'
import { label, SHORTCUTS } from '../lib/shortcuts'
import type { Theme } from '../lib/theme'
import { settingsPath } from '../settings/sections'
import { titleOf } from '../workspace/recents'
import { Mark } from '../ui/Mark'
import type { Workspace } from '../workspace/context'
import type { PaletteItem } from './palette'

export interface PaletteActions {
  workspace: Workspace
  /** Navigate to an address inside the app, with state for the page to act on. */
  go: (path: string, state?: unknown) => void
  ask: (question: string) => void
  call: CallStatus
  railed: boolean
  /** Absent while the window is too narrow for the full sidebar. */
  toggleSidebar?: () => void
  openActivity: () => void
  theme: Theme
  setTheme: (theme: Theme) => void
}

export function paletteItems(query: string, actions: PaletteActions): PaletteItem[] {
  const { workspace, go } = actions
  const question = query.trim()
  const items: PaletteItem[] = []

  if (question) {
    items.push({
      id: 'ask',
      group: 'Ask',
      label: `Ask Veronica: “${question}”`,
      icon: <Mark size={18} />,
      run: () => actions.ask(question),
    })
  }

  items.push(
    {
      id: 'go:new',
      group: 'Go to',
      label: 'New chat',
      icon: <SquarePen size={18} />,
      hint: label(SHORTCUTS.newChat),
      keywords: ['conversation', 'start'],
      run: workspace.newChat,
    },
    { id: 'go:call', group: 'Go to', label: 'Call', icon: <AudioLines size={18} />, keywords: ['voice', 'talk', 'speak'], run: () => go(ROUTES.call) },
    { id: 'go:memory', group: 'Go to', label: 'Memory', icon: <Brain size={18} />, keywords: ['remember', 'facts', 'documents', 'files'], run: () => go(ROUTES.memory) },
    { id: 'go:voices', group: 'Go to', label: 'Voices', icon: <AudioWaveform size={18} />, keywords: ['clone', 'studio', 'speech'], run: () => go(ROUTES.voices) },
    {
      id: 'go:settings',
      group: 'Go to',
      label: 'Settings',
      icon: <Settings size={18} />,
      keywords: ['preferences', 'personality', 'audio', 'microphone', 'hotkeys', 'about', 'version'],
      run: () => go(ROUTES.settings),
    },
    ...settingsLinks(actions),
    ...callActions(actions),
    {
      id: 'act:activity',
      group: 'Actions',
      label: 'Show memory activity',
      icon: <History size={18} />,
      keywords: ['recent', 'undo', 'remembered', 'forgot'],
      run: actions.openActivity,
    },
    ...themeActions(actions),
  )
  if (actions.toggleSidebar) {
    items.push({
      id: 'act:sidebar',
      group: 'Actions',
      label: actions.railed ? 'Expand the sidebar' : 'Collapse the sidebar',
      icon: actions.railed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />,
      run: actions.toggleSidebar,
    })
  }

  for (const project of workspace.projects) {
    items.push({
      id: `project:${project.id}`,
      group: 'Projects',
      label: project.name,
      icon: <Folder size={18} />,
      hint: `${project.conversation_count} ${project.conversation_count === 1 ? 'conversation' : 'conversations'}`,
      run: () => workspace.selectProject(project.id),
    })
  }

  for (const conversation of workspace.conversations) {
    items.push({
      id: `conversation:${conversation.id}`,
      group: 'Conversations',
      label: titleOf(conversation),
      icon: <MessageSquare size={18} />,
      hint: relativeTime(conversation.updated_at) ?? undefined,
      run: () => workspace.openConversation(conversation.id),
    })
  }

  return items
}

/** Straight to one part of Settings, for whoever types what they are after. */
function settingsLinks({ go }: PaletteActions): PaletteItem[] {
  return [
    {
      id: 'go:personality',
      group: 'Go to',
      label: 'Her personality',
      icon: <Sparkles size={18} />,
      keywords: ['settings', 'persona', 'character', 'tone'],
      searchOnly: true,
      run: () => go(settingsPath('personality')),
    },
    {
      id: 'go:desktop',
      group: 'Go to',
      label: 'Shortcuts and startup',
      icon: <Keyboard size={18} />,
      keywords: ['settings', 'hotkeys', 'keyboard', 'tray', 'windows', 'desktop'],
      searchOnly: true,
      run: () => go(settingsPath('desktop')),
    },
    {
      id: 'go:about',
      group: 'Go to',
      label: 'About Veronica',
      icon: <Info size={18} />,
      keywords: ['settings', 'version', 'patch notes', 'changelog', 'new'],
      searchOnly: true,
      run: () => go(settingsPath('about')),
    },
  ]
}

const THEME_ACTIONS: Record<Theme, { label: string; icon: ReactNode }> = {
  dark: { label: 'Switch to the dark theme', icon: <Moon size={18} /> },
  light: { label: 'Switch to the light theme', icon: <Sun size={18} /> },
  system: { label: 'Follow the system theme', icon: <Monitor size={18} /> },
}

/** The two themes not in use, for whoever types "dark" or "theme". */
function themeActions({ theme, setTheme }: PaletteActions): PaletteItem[] {
  return (Object.keys(THEME_ACTIONS) as Theme[])
    .filter((option) => option !== theme)
    .map((option) => ({
      id: `act:theme-${option}`,
      group: 'Actions',
      label: THEME_ACTIONS[option].label,
      icon: THEME_ACTIONS[option].icon,
      keywords: ['theme', 'appearance', 'mode', 'colour', 'color'],
      searchOnly: true,
      run: () => setTheme(option),
    }))
}

/** Start a call, or while one is on, end it or mute it: from anywhere. */
function callActions({ call, go }: PaletteActions): PaletteItem[] {
  if (!call.inCall) {
    return [
      {
        id: 'act:call',
        group: 'Actions',
        label: 'Start a call',
        icon: <Phone size={18} />,
        keywords: ['voice', 'talk', 'speak'],
        // The call page starts it on arrival: a page with unsaved changes can still
        // refuse the move, and the microphone must not go live behind its dialog.
        run: () => go(ROUTES.call, { startCall: true }),
      },
    ]
  }
  return [
    { id: 'act:end', group: 'Actions', label: 'End the call', icon: <PhoneOff size={18} />, keywords: ['hang up', 'stop'], run: call.endCall },
    {
      id: 'act:mute',
      group: 'Actions',
      label: call.muted ? 'Unmute the microphone' : 'Mute the microphone',
      icon: call.muted ? <Mic size={18} /> : <MicOff size={18} />,
      run: call.toggleMute,
    },
  ]
}
