// Shown at the top of the content when she started without part of what she needs:
// what happened and what to do, in the app's words (`problemCopy`). It goes when a
// restart fixes it, as soon as the event lane reaches the new backend. Two are put
// right from here: an activity log changed outside Veronica, by marking it as reviewed,
// and her memory or voices locked to another account, with the recovery key saved.

import { UseRecoveryKey } from '../recovery/UseRecoveryKey'
import { Notice, type NoticeTone } from '../ui/Feedback'
import styles from './AppShell.module.css'
import { AuditReview } from './AuditReview'
import { problemCopy } from './problemCopy'
import { useProblems } from './useProblems'

// What stops her comes first: a memory she can't open before voices she speaks without,
// which a data folder moved to another account brings together.
const ORDER: Record<NoticeTone, number> = { critical: 0, attention: 1, info: 2, positive: 3 }

export function ProblemBanner() {
  const { problems, refresh } = useProblems()
  if (problems.length === 0) return null
  const shown = problems
    .map((problem) => ({ problem, copy: problemCopy(problem) }))
    .sort((a, b) => ORDER[a.copy.tone ?? 'critical'] - ORDER[b.copy.tone ?? 'critical'])
  // One offer of the recovery key, on the first notice it answers: it opens both.
  const recoverable = shown.find(({ problem }) => problem.detail.recoverable === 1)?.problem.code
  return (
    <div className={styles.banner}>
      {shown.map(({ problem, copy }) => {
        const action =
          problem.code === 'audit_broken' ? (
            <AuditReview onReviewed={refresh} />
          ) : problem.code === recoverable ? (
            <UseRecoveryKey mode="use" />
          ) : undefined
        return (
          <Notice key={problem.code} tone={copy.tone ?? 'critical'} live title={copy.title} action={action}>
            {copy.body}
          </Notice>
        )
      })}
    </div>
  )
}
