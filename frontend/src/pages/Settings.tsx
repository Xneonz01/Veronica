// Settings: how the app looks, who she is, how calls start, the desktop app, the web, her
// tool servers, how her data is kept, and which version this is. Every change applies at once, everywhere, with no restart.
//
// The open section is in the address (#/settings?section=calls), so the palette
// and other pages can link straight to one, and the back button leaves Settings
// rather than stepping through its tabs.

import type { ComponentType } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AboutSettings } from '../settings/AboutSettings'
import { CallSettings } from '../settings/CallSettings'
import { DesktopSettings } from '../settings/DesktopSettings'
import { GeneralSettings } from '../settings/GeneralSettings'
import { PersonalitySettings } from '../settings/PersonalitySettings'
import { PrivacySettings } from '../settings/PrivacySettings'
import { SETTINGS_SECTIONS, type SettingsSection } from '../settings/sections'
import { ToolServerSettings } from '../settings/ToolServerSettings'
import { WebSettings } from '../settings/WebSettings'
import { panelProps } from '../ui/tabPanel'
import { Tabs } from '../ui/Tabs'
import styles from './Settings.module.css'

const PANEL: Record<SettingsSection, ComponentType> = {
  general: GeneralSettings,
  personality: PersonalitySettings,
  calls: CallSettings,
  desktop: DesktopSettings,
  web: WebSettings,
  tools: ToolServerSettings,
  privacy: PrivacySettings,
  about: AboutSettings,
}

export function Settings() {
  const [params, setParams] = useSearchParams()
  const section = SETTINGS_SECTIONS.find((item) => item.value === params.get('section'))?.value ?? 'general'
  const Panel = PANEL[section]

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>Settings</h1>
        <p className={styles.lead}>Every change applies at once, everywhere, with no restart.</p>
      </header>
      <div className={styles.body}>
        <Tabs<SettingsSection>
          id="settings"
          label="Settings"
          value={section}
          tabs={SETTINGS_SECTIONS}
          onChange={(next) => setParams(next === 'general' ? {} : { section: next }, { replace: true })}
        />
        <section {...panelProps('settings', section)} className={styles.panel}>
          <Panel />
        </section>
      </div>
    </div>
  )
}
