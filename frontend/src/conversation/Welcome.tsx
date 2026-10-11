// A new conversation: her presence, a greeting for the time of day, and a few
// ways in. A starter fills the composer for you to finish rather than sending on
// your behalf; the composer sits right under it, where your eyes already are.

import { Mark } from '../ui/Mark'
import styles from './Welcome.module.css'

const STARTERS = [
  { label: 'Plan my week', prompt: 'Help me plan my week. This is what I have on: ' },
  { label: 'What do you remember about me?', prompt: 'What do you remember about me?' },
  { label: 'Help me write something', prompt: 'Help me write ' },
  { label: 'Think something through', prompt: "I'd like to think something through with you: " },
]

function greeting(now: Date): string {
  const hour = now.getHours()
  if (hour >= 5 && hour < 12) return 'Good morning.'
  if (hour >= 12 && hour < 18) return 'Good afternoon.'
  return 'Good evening.'
}

export function Welcome({ project, onStarter }: { project: string | null; onStarter: (prompt: string) => void }) {
  return (
    <div className={styles.welcome}>
      <span className={styles.presence}>
        <Mark size={34} />
      </span>
      <h1 className={styles.hello}>{greeting(new Date())}</h1>
      <p className={styles.ask}>
        {project ? (
          <>
            A new chat in <strong>{project}</strong>. What shall we work on?
          </>
        ) : (
          'What shall we work on?'
        )}
      </p>
      <ul className={styles.starters} aria-label="Ways to start">
        {STARTERS.map((starter) => (
          <li key={starter.label}>
            <button type="button" className={styles.starter} onClick={() => onStarter(starter.prompt)}>
              {starter.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
