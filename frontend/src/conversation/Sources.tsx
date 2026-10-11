// Where an answer came from: numbered pills under it, each opening the exact
// passage she was shown. The passage is stored with the message, so it still
// opens after the document itself has been removed.

import { FileText } from 'lucide-react'
import type { Citation } from '../chat/types'
import { Sheet } from '../ui/Sheet'
import styles from './Sources.module.css'

export function Sources({ citations, onOpen }: { citations: readonly Citation[]; onOpen: (citation: Citation) => void }) {
  return (
    <div className={styles.sources}>
      <span className={styles.label}>Sources</span>
      <ul className={styles.list}>
        {citations.map((citation) => (
          <li key={citation.n}>
            <button
              type="button"
              className={styles.pill}
              aria-label={`Source ${citation.n}: ${citation.title}`}
              onClick={() => onOpen(citation)}
            >
              <span className={styles.number} aria-hidden="true">
                {citation.n}
              </span>
              <span className={styles.title}>{citation.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function SourceSheet({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  return (
    <Sheet
      open={citation !== null}
      onClose={onClose}
      title={citation?.title ?? ''}
      description={citation ? `Source ${citation.n} · passage ${citation.ordinal + 1}` : undefined}
    >
      {citation?.text ? (
        <figure className={styles.passage}>
          <blockquote className={styles.quote}>{citation.text}</blockquote>
          <figcaption className={styles.caption}>
            <FileText size={14} aria-hidden="true" />
            The passage I was shown, word for word.
          </figcaption>
        </figure>
      ) : (
        <p className={styles.missing}>
          This source was cited before passages were kept with answers, so there is nothing to show here.
        </p>
      )}
    </Sheet>
  )
}
