// A decision for the owner, not a feature: the light theme's page in the
// current porcelain (celadon, cool) next to a warm ivory. Both render the same
// turn from the real rules; each island swaps only the primitive ramp, and
// [data-palette] makes the semantic tokens rebuild from it. The one not chosen
// is deleted with this file.

import { Transcript } from '../conversation/Transcript'
import { cx } from '../ui/cx'
import { ContrastTable } from './ContrastTable'
import { roofAnswer, roofQuestion } from './fixtures'
import styles from './LightStudy.module.css'
import { Section } from './Specimen'

const OPTIONS = [
  { palette: 'porcelain', title: 'Porcelain', note: 'Current. Celadon at the light end, cool ink.' },
  { palette: 'ivory', title: 'Ivory', note: 'Warm paper, warm ink. Closer to a notebook.' },
] as const

export function LightStudy() {
  return (
    <Section
      id="light-study"
      title="Light theme study"
      lead="The same turn on two light grounds. Pick one; the other is removed. Dark is not affected."
    >
      <div className={styles.options}>
        {OPTIONS.map((option) => (
          <div
            key={option.palette}
            data-theme="light"
            data-palette={option.palette}
            className={cx(styles.option, option.palette === 'ivory' && styles.ivory)}
          >
            <header className={styles.head}>
              <h3 className={styles.title}>{option.title}</h3>
              <p className={styles.note}>{option.note}</p>
            </header>
            <div className={styles.turn}>
              <Transcript
                messages={[roofQuestion, roofAnswer]}
                state="idle"
                conversationId={1}
                stoppedAfter={null}
                problem={null}
                onRetry={() => undefined}
                onDismissProblem={() => undefined}
                receipts={[]}
                onUndoReceipt={() => undefined}
              />
            </div>
            <details className={styles.measured}>
              <summary className={styles.summary}>Contrast, measured</summary>
              <ContrastTable />
            </details>
          </div>
        ))}
      </div>
    </Section>
  )
}
