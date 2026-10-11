// General: how the app looks, and how much you hear about what she remembers.

import { useNotifications } from '../notify/useNotifications'
import type { Verbosity } from '../notify/settings'
import { Segmented } from '../ui/Segmented'
import { Switch } from '../ui/Switch'
import { Group, Row } from './Group'
import { ThemeChoice } from './ThemeChoice'

const ALERTS: Record<Verbosity, string> = {
  loud: 'A card for everything she writes to her memory.',
  subtle: 'A card when you asked her to remember something, or she corrected herself. Anything else only marks the bell.',
  silent: 'No cards and no sounds. The bell still marks what is new.',
}

export function GeneralSettings() {
  const { settings } = useNotifications()
  const silent = settings.verbosity === 'silent'
  return (
    <>
      <Group title="Appearance" lead="System follows your computer's light or dark setting." bare>
        <ThemeChoice />
      </Group>
      <Group title="Memory alerts" lead="What you see and hear when she remembers something.">
        <Row label="Alerts" description={ALERTS[settings.verbosity]}>
          <Segmented<Verbosity>
            label="Memory alerts"
            size="sm"
            value={settings.verbosity}
            onChange={settings.setVerbosity}
            options={[
              { value: 'loud', label: 'Everything' },
              { value: 'subtle', label: 'Important' },
              { value: 'silent', label: 'Silent' },
            ]}
          />
        </Row>
        <Switch
          label="Sounds"
          description={silent ? 'Off while alerts are silent.' : 'A soft tone when she remembers, changes or forgets something.'}
          checked={settings.sounds && !silent}
          disabled={silent}
          onChange={settings.setSounds}
        />
      </Group>
    </>
  )
}
