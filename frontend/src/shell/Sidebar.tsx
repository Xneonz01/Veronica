// The sidebar: a new chat, search, the places she lives, your projects and
// conversations, and whether her backend is there. Collapsed, it becomes a rail of
// icons that keeps every destination one click away.

import { AudioLines, AudioWaveform, Bell, Brain, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useLaneStatus } from '../audio/useAppEvents'
import { CallDock } from '../call/CallDock'
import type { LaneStatus } from '../lib/eventsLane'
import { ROUTES } from '../lib/routes'
import { ariaKeys, label, SHORTCUTS } from '../lib/shortcuts'
import { useNotifications } from '../notify/useNotifications'
import { IconButton } from '../ui/Button'
import { cx } from '../ui/cx'
import { Kbd } from '../ui/Kbd'
import { Mark } from '../ui/Mark'
import { useWorkspace } from '../workspace/context'
import { ProjectList } from './ProjectList'
import { RailContext } from './rail'
import { Recents } from './Recents'
import styles from './Sidebar.module.css'
import { RowButton, RowLink } from './SidebarRow'

export type SidebarLayout = 'full' | 'rail'

export function Sidebar({
  layout,
  onToggle,
  onSearch,
  onActivity,
}: {
  layout: SidebarLayout
  /** Absent while the window is too narrow for the full sidebar. */
  onToggle?: () => void
  onSearch: () => void
  onActivity: () => void
}) {
  const rail = layout === 'rail'
  const { newChat } = useWorkspace()
  const { unseen } = useNotifications()
  const toggle = onToggle && (
    <IconButton
      size="sm"
      label={rail ? 'Expand sidebar' : 'Collapse sidebar'}
      icon={rail ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
      onClick={onToggle}
    />
  )

  return (
    <RailContext.Provider value={rail}>
      <aside className={cx(styles.sidebar, rail && styles.rail)} aria-label="Sidebar">
        <div className={styles.header}>
          <span className={styles.brand}>
            <Mark size={24} />
            {!rail && <span className={styles.wordmark}>Veronica</span>}
          </span>
          {/* One place in both layouts: moved between parents, the button would be a
              new one, and the keyboard on it would fall to the page. */}
          {toggle}
        </div>

        <div className={styles.actions}>
          <RowButton
            icon={
              <span className={styles.newChat}>
                <Plus size={14} strokeWidth={2.5} aria-hidden="true" />
              </span>
            }
            label="New chat"
            title={`New chat (${label(SHORTCUTS.newChat)})`}
            onClick={newChat}
          />
          <RowButton
            icon={<Search size={18} aria-hidden="true" />}
            label="Search"
            meta={<Kbd>{label(SHORTCUTS.palette)}</Kbd>}
            aria-keyshortcuts={ariaKeys(SHORTCUTS.palette)}
            onClick={onSearch}
          />
        </div>

        <nav className={styles.nav} aria-label="Primary">
          <RowLink to={ROUTES.call} icon={<AudioLines size={18} aria-hidden="true" />} label="Call" />
          <RowLink to={ROUTES.memory} icon={<Brain size={18} aria-hidden="true" />} label="Memory" />
          <RowLink to={ROUTES.voices} icon={<AudioWaveform size={18} aria-hidden="true" />} label="Voices" />
        </nav>

        {rail ? (
          <div className={styles.spacer} />
        ) : (
          <div className={styles.scroll}>
            <ProjectList />
            <Recents />
          </div>
        )}

        <CallDock />
        <footer className={styles.footer}>
          <Status />
          <span className={styles.footerActions}>
            <span className={styles.bell}>
              <IconButton
                size="sm"
                label={unseen > 0 ? `Memory activity, ${unseen} new` : 'Memory activity'}
                icon={<Bell size={17} aria-hidden="true" />}
                onClick={onActivity}
              />
              {unseen > 0 && <span className={styles.unseen} aria-hidden="true" />}
            </span>
            <NavLink
              to={ROUTES.settings}
              className={styles.iconLink}
              aria-label="Settings"
              title="Settings"
            >
              <Settings size={17} aria-hidden="true" />
            </NavLink>
          </span>
        </footer>
      </aside>
    </RailContext.Provider>
  )
}

const STATUS: Record<LaneStatus, { text: string; hint: string }> = {
  online: { text: 'On this computer', hint: 'Connected. Everything runs on this computer.' },
  connecting: { text: 'Connecting', hint: "Reaching Veronica's backend on this computer." },
  offline: { text: 'Backend offline', hint: "Veronica's backend isn't answering. It reconnects by itself." },
}

function Status() {
  const status = useLaneStatus()
  const { text, hint } = STATUS[status]
  return (
    <span className={styles.status} title={hint}>
      <span className={styles.statusDot} data-status={status} aria-hidden="true" />
      <span className={styles.statusText}>{text}</span>
    </span>
  )
}
