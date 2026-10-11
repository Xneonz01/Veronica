// The Memory page: what she knows about you, what she wants your word on, and the
// documents she can cite. Every change, made here, by her, or in another window,
// arrives over the event lane and shows without a refresh.

import { Brain, Inbox, FileText } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useAppEvents } from '../audio/useAppEvents'
import { DocumentList } from '../memory/DocumentList'
import { KIND, LEARNING } from '../memory/labels'
import { MemoryList } from '../memory/MemoryList'
import { ReviewList } from '../memory/ReviewList'
import type { LearningMode, MemoryKind } from '../memory/types'
import { useDocuments } from '../memory/useDocuments'
import { useLearningMode } from '../memory/useLearningMode'
import { useMemory } from '../memory/useMemory'
import { useNotifications } from '../notify/useNotifications'
import { Button } from '../ui/Button'
import { EmptyState, Notice, Skeleton } from '../ui/Feedback'
import { SearchField } from '../ui/Field'
import { Segmented } from '../ui/Segmented'
import { panelProps } from '../ui/tabPanel'
import { Tabs } from '../ui/Tabs'
import styles from './Memory.module.css'

type View = 'memories' | 'review' | 'documents'
type Filter = 'all' | MemoryKind

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'profile', label: KIND.profile },
  { value: 'preference', label: 'Preferences' },
  { value: 'fact', label: 'Facts' },
  { value: 'summary', label: 'Call recaps' },
]

export function Memory() {
  const memory = useMemory()
  const documents = useDocuments()
  const learning = useLearningMode()
  const { markSeen } = useNotifications()
  const [view, setView] = useState<View>('memories')
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')

  // Arriving here is seeing what she remembered, so the sidebar's dot clears.
  useEffect(() => markSeen(), [markSeen])

  useAppEvents({
    onMemoryUpdated: () => void memory.refresh(),
    onMemoryLearned: () => void memory.refresh(),
    onRagUpdated: () => void documents.refresh(),
    onLearningMode: (next) => {
      if (next === 'proactive' || next === 'review_only') learning.setMode(next)
    },
    onConnected: () => {
      void memory.refresh()
      void documents.refresh()
      void learning.refresh()
    },
  })

  const candidates = useMemo(() => memory.memories.filter((item) => item.pending), [memory.memories])
  // What she keeps about you first, then what you pinned, then the rest newest
  // first, as the server sends them.
  const kept = useMemo(() => {
    const rank = (item: { kind: string; pinned: boolean }) => (item.kind === 'profile' ? 0 : item.pinned ? 1 : 2)
    return memory.memories.filter((item) => !item.pending).sort((a, b) => rank(a) - rank(b))
  }, [memory.memories])
  const shown = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return kept.filter(
      (item) =>
        (filter === 'all' || item.kind === filter || (filter === 'fact' && item.kind === 'episodic')) &&
        (!needle || item.content.toLocaleLowerCase().includes(needle)),
    )
  }, [kept, filter, query])

  const problem = memory.error ?? documents.error ?? learning.error
  // Clears the one on screen, in the order `problem` picks it.
  const dismissProblem = () => {
    if (memory.error) memory.setError(null)
    else if (documents.error) documents.setError(null)
    else learning.setError(null)
  }
  // A list still loading, or that never loaded, is unknown rather than empty: no
  // count, and no "nothing yet".
  const memoriesUnknown = memory.loading || (memory.error?.severity === 'fatal' && memory.memories.length === 0)
  const documentsUnknown = documents.loading || (documents.error?.severity === 'fatal' && documents.documents.length === 0)

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.titles}>
          <h1 className={styles.title}>Memory</h1>
          <p className={styles.lead}>What Veronica knows about you, and the documents she can cite. It all stays on this computer.</p>
        </div>
        <div className={styles.learning}>
          <span className={styles.learningLabel}>How she learns</span>
          <Segmented<LearningMode>
            label="How she learns"
            size="sm"
            value={learning.mode}
            onChange={(next) => void learning.update(next)}
            options={[
              { value: 'proactive', label: LEARNING.proactive.label },
              { value: 'review_only', label: LEARNING.review_only.label },
            ]}
          />
          <span className={styles.learningMeans}>{LEARNING[learning.mode].means}</span>
        </div>
      </header>

      <div className={styles.body}>
        {problem && (
          <Notice
            tone={problem.severity === 'fatal' ? 'critical' : 'attention'}
            live
            action={
              // A list that didn't load can be asked for again; a change that
              // failed has already been put back, so there is only the note to clear.
              problem.severity === 'fatal' ? (
                <Button size="sm" variant="secondary" onClick={() => void Promise.all([memory.refresh(), documents.refresh(), learning.refresh()])}>
                  Try again
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={dismissProblem}>
                  Dismiss
                </Button>
              )
            }
          >
            {problem.message}
          </Notice>
        )}

        <Tabs<View>
          id="memory"
          label="Memory"
          value={view}
          onChange={setView}
          tabs={[
            { value: 'memories', label: 'Memories', count: memoriesUnknown ? undefined : kept.length },
            { value: 'review', label: 'To review', count: memoriesUnknown ? undefined : candidates.length },
            { value: 'documents', label: 'Documents', count: documentsUnknown ? undefined : documents.documents.length },
          ]}
        />

        <section {...panelProps('memory', view)} className={styles.panel}>
          {view === 'memories' && (
            <>
              {/* Nothing to search yet, nothing to search with. */}
              {kept.length > 0 && (
                <div className={styles.filters}>
                  <SearchField label="Search memories" placeholder="Search what she remembers" value={query} onChange={(event) => setQuery(event.target.value)} />
                  <Segmented<Filter> label="Show" size="sm" value={filter} onChange={setFilter} options={FILTERS} />
                </div>
              )}
              {memory.loading ? (
                <Loading />
              ) : shown.length > 0 ? (
                <MemoryList
                  memories={shown}
                  onPin={(id, pinned) => void memory.edit(id, { pinned })}
                  onEdit={(id, content) => void memory.edit(id, { content })}
                  onForget={(id) => void memory.remove(id)}
                />
              ) : kept.length > 0 ? (
                <EmptyState title="Nothing matches">Try other words, or show every kind of memory.</EmptyState>
              ) : memoriesUnknown ? null : (
                <EmptyState icon={<Brain size={22} aria-hidden="true" />} title="Nothing remembered yet">
                  Tell her something worth keeping, or talk for a while: what she learns about you appears here, and you can edit or forget any of it.
                </EmptyState>
              )}
            </>
          )}

          {view === 'review' && memory.loading && <Loading />}
          {view === 'review' &&
            !memoriesUnknown &&
            (candidates.length > 0 ? (
              <ReviewList
                candidates={candidates}
                onKeep={(id) => void memory.review(id, 'approve')}
                onDrop={(id) => void memory.review(id, 'reject')}
              />
            ) : (
              <EmptyState icon={<Inbox size={22} aria-hidden="true" />} title="Nothing to review">
                When she isn't sure about something she picked up, or learning is set to ask you first, it waits here until you keep or drop it.
              </EmptyState>
            ))}

          {view === 'documents' && (
            <>
              <DocumentList
                documents={documents.documents}
                onAddFile={documents.addFile}
                onAddText={documents.addText}
                onRemove={(id) => void documents.remove(id)}
              />
              {!documents.loading && !documentsUnknown && documents.documents.length === 0 && (
                <EmptyState icon={<FileText size={22} aria-hidden="true" />} title="No documents yet">
                  Add notes, manuals or anything you'd like her to answer from. She cites the passage she used, so you can check it.
                </EmptyState>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function Loading() {
  return (
    <div className={styles.loading} role="status">
      <span className="visually-hidden">Loading memories</span>
      {[82, 64, 74, 58].map((width) => (
        <div key={width} className={styles.loadingRow}>
          <Skeleton width={`${width}%`} height={14} />
          <Skeleton width="30%" height={11} />
        </div>
      ))}
    </div>
  )
}
