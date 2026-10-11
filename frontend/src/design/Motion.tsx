// The motion language, replayable. Each animation says something: a result
// unfolds because it was produced, a panel pops from its anchor, a list rises
// in order, and her mark breathes only while she is working.

import { useState, type ReactNode } from 'react'
import { Button } from '../ui/Button'
import { Mark } from '../ui/Mark'
import styles from './Motion.module.css'
import { Section, Tile, Tiles } from './Specimen'

const ROWS = ['Roof repairs', 'Weekly plan', 'Birthday gift ideas', 'Tax documents']

export function Motion() {
  return (
    <Section
      id="motion"
      title="Motion"
      lead="Three durations, four curves. Motion carries information or it doesn't ship, and reduced motion keeps every state change without the travel."
    >
      <Tiles>
        <Demo label="Unfold" note="A result arriving · 340 ms, standard">
          <div className={styles.card}>
            <span className={styles.cardTitle}>Searched your files</span>
            <span className={styles.cardMeta}>3 found</span>
          </div>
        </Demo>
        <Demo label="Pop" note="A panel from its anchor · 220 ms, spring">
          <div className={styles.popWrap}>
            <span className={styles.anchor} />
            <div className={styles.pop}>
              <span className={styles.popLine} />
              <span className={styles.popLine} />
              <span className={styles.popLine} />
            </div>
          </div>
        </Demo>
        <Demo label="Rise, in order" note="A list arriving · 40 ms apart">
          <ul className={styles.list}>
            {ROWS.map((row, index) => (
              <li key={row} className={styles.rise} style={{ animationDelay: `${index * 40}ms` }}>
                {row}
              </li>
            ))}
          </ul>
        </Demo>
        <Tile label="Breathe" note="Her mark while she works · 2.4 s, in-out, loops">
          <div className={styles.breatheStage}>
            <span className={styles.breathe}>
              <Mark size={40} />
            </span>
          </div>
        </Tile>
      </Tiles>
    </Section>
  )
}

/** A tile whose content re-mounts on Replay, which replays its entrance. */
function Demo({ label, note, children }: { label: string; note: string; children: ReactNode }) {
  const [run, setRun] = useState(0)
  return (
    <Tile label={label} note={note}>
      <div className={styles.demoStage}>
        <div key={run} className={styles.subject}>
          {children}
        </div>
      </div>
      <Button size="sm" variant="ghost" onClick={() => setRun((was) => was + 1)}>
        Replay
      </Button>
    </Tile>
  )
}
