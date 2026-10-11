// The parts of the Settings page, in order. Kept apart from the page so the command
// palette can link to a part without importing the page.

export type SettingsSection = 'general' | 'personality' | 'calls' | 'desktop' | 'web' | 'privacy' | 'about'

export const SETTINGS_SECTIONS: readonly { value: SettingsSection; label: string }[] = [
  { value: 'general', label: 'General' },
  { value: 'personality', label: 'Personality' },
  { value: 'calls', label: 'Calls' },
  { value: 'desktop', label: 'Desktop' },
  { value: 'web', label: 'Web' },
  { value: 'privacy', label: 'Privacy' },
  { value: 'about', label: 'About' },
]

/** The address of one part of Settings. */
export function settingsPath(section: SettingsSection): string {
  return section === 'general' ? '/settings' : `/settings?section=${section}`
}
