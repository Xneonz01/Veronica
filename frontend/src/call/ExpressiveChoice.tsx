// Whether her voice laughs, chuckles or sighs where a reply calls for it. Kept on this
// computer and sent to a call as it changes, so it applies from her next reply.

import { useEffect, useState } from 'react'
import { onExpressiveChange, readExpressive, writeExpressive } from '../notify/settings'
import { Switch } from '../ui/Switch'

export function ExpressiveChoice() {
  const [on, setOn] = useState(readExpressive)
  useEffect(() => onExpressiveChange(setOn), []) // a change made in the other place
  return (
    <Switch
      label="Expressive voice"
      description="She can laugh, chuckle or sigh when the moment calls for it. The sound is only heard, never written in the conversation."
      checked={on}
      onChange={writeExpressive}
    />
  )
}
