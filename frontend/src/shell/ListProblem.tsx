// A sidebar list that couldn't be loaded: why, in a sentence, and a way to try
// again. Without it an unreachable backend would read as "you have nothing yet".

import { Button } from '../ui/Button'
import styles from './Recents.module.css'

export function ListProblem({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={styles.problem} role="status">
      <p>{message}</p>
      <Button size="sm" variant="ghost" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}
