// Calls: how she opens one, and the microphone and speakers she uses. The same
// controls as the call's own settings, so a change made in either place is the same
// change.

import { AudioDevices } from '../call/AudioDevices'
import { GreetingChoice } from '../call/GreetingChoice'
import { Group } from './Group'

export function CallSettings() {
  return (
    <>
      <Group title="Greeting" lead="Applies from the next call.">
        <GreetingChoice />
      </Group>
      <Group title="Microphone and speakers" lead="Used for calls and for recording voice clips. A change applies at once, even mid-call.">
        <AudioDevices />
      </Group>
    </>
  )
}
