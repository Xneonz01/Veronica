// The call without a call: her orb in each state, the dock the sidebar shows
// during a call, and the live exchange. The orb hears nothing here, so it shows
// each state at rest; speaking and listening move with real sound in a call.

import type { RefObject } from 'react'
import type { AmplitudeTaps, CallState } from '../audio/useRealtimeCall'
import { CallDock } from '../call/CallDock'
import { STATE_LABEL } from '../call/callCopy'
import { CallStatusContext, type CallStatus } from '../call/context'
import { LiveLines } from '../call/LiveLines'
import { Orb } from '../call/Orb'
import styles from './CallSpecimens.module.css'
import { Section, Tile, Tiles } from './Specimen'

const SILENCE: RefObject<AmplitudeTaps> = { current: { mic: null, playback: null } }

const MOODS: { state: CallState; note: string }[] = [
  { state: 'idle', note: 'At rest, breathing' },
  { state: 'warming_up', note: 'Loading her voice' },
  { state: 'listening', note: 'A ring in your colour follows your voice' },
  { state: 'thinking', note: 'An arc circles the core' },
  { state: 'speaking', note: 'Rings leave her, as loud as she is' },
  { state: 'error', note: 'Dim, in the colour of failure' },
]

const DOCKED: CallStatus = {
  state: 'speaking',
  inCall: true,
  muted: false,
  since: Date.now() - 134_000,
  ripple: 0,
  startCall: async () => undefined,
  endCall: () => undefined,
  toggleMute: () => undefined,
  switchInput: async () => undefined,
  switchOutput: async () => undefined,
}

export function CallSpecimens() {
  return (
    <Section id="call" title="Call" lead="Her presence in a call: the orb in each state, the dock while you are elsewhere, and the exchange as it happens.">
      <Tiles>
        {MOODS.map(({ state, note }) => (
          <Tile key={state} label={STATE_LABEL[state]} note={note}>
            <div className={styles.stage}>
              <Orb state={state} amplitude={SILENCE} size={180} />
            </div>
          </Tile>
        ))}
      </Tiles>
      <Tiles wide>
        <Tile label="The dock" note="In the sidebar, on every page but the call's own">
          <div className={styles.dock}>
            <CallStatusContext.Provider value={DOCKED}>
              <CallDock />
            </CallStatusContext.Provider>
          </div>
        </Tile>
        <Tile label="The exchange" note="Yours softer until she has it; hers with a caret while she speaks">
          <LiveLines
            finalText="What's on tomorrow morning?"
            partial=""
            reply="The dentist at half past ten, then nothing until the design review"
            state="speaking"
          />
        </Tile>
        <Tile label="A claim nothing backed" note="What her reply said she did and didn't, apart from her words">
          <LiveLines
            finalText="Forget that I go to the gym on Tuesdays."
            partial=""
            reply="Done. I've forgotten that you go to the gym on Tuesdays."
            caveat="My memory didn't actually change just now. Ask me again if you want it to."
            state="listening"
          />
        </Tile>
      </Tiles>
    </Section>
  )
}
