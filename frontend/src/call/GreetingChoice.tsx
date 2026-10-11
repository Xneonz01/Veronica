// How she opens a call: whether she says hello first, how much, and whether she
// sometimes picks up from the last call. Kept on this computer and sent when a call
// starts, so a change applies from the next call.

import { useState } from 'react'
import { readGreeting, writeGreeting, type GreetingSettings } from '../notify/settings'
import { Segmented } from '../ui/Segmented'
import { Switch } from '../ui/Switch'
import styles from './GreetingChoice.module.css'

export function GreetingChoice() {
  const [greeting, setGreeting] = useState<GreetingSettings>(readGreeting)
  const update = (patch: Partial<GreetingSettings>) => {
    const next = { ...greeting, ...patch }
    setGreeting(next)
    writeGreeting(next)
  }
  return (
    <div className={styles.choice}>
      <Switch
        label="Greet me when a call starts"
        description="She says hello first, so you know she's listening."
        checked={greeting.enabled}
        onChange={(enabled) => update({ enabled })}
      />
      {greeting.enabled && (
        <>
          <div className={styles.length}>
            <span className={styles.lengthLabel}>How much she says</span>
            <Segmented
              label="How much she says"
              size="sm"
              value={greeting.length}
              onChange={(length) => update({ length })}
              options={[
                { value: 'brief', label: 'Brief' },
                { value: 'warm', label: 'Warm' },
              ]}
            />
          </div>
          <Switch
            label="Pick up where we left off"
            description="Now and then she opens with a line about your last call."
            checked={greeting.callback}
            onChange={(callback) => update({ callback })}
          />
        </>
      )}
    </div>
  )
}
