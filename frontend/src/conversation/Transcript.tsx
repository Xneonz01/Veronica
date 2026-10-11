// The conversation, turn by turn. Your question is set as the heading of the
// answer that follows, and the page reads like a document rather than a chat log.
//
// Scrolling is anchored to turns: a question you send moves to the top of the
// view and her answer writes itself below it, with nothing yanking the page
// while you read. An opened conversation starts at its latest turn.

import { ArrowDown, Square } from 'lucide-react'
import { useLayoutEffect, useRef, useState } from 'react'
import type { ChatMessage, ChatState, Citation } from '../chat/types'
import type { Notification } from '../notify/types'
import { Button } from '../ui/Button'
import { cx } from '../ui/cx'
import { Notice } from '../ui/Feedback'
import { Answer } from './Answer'
import type { ChatProblem } from './chatProblems'
import { MemoryLine } from './MemoryLine'
import { SourceSheet } from './Sources'
import styles from './Transcript.module.css'
import { isJustSent, toTurns } from './turns'

export type TranscriptProps = {
  messages: readonly ChatMessage[]
  state: ChatState
  conversationId: number | null
  /** The question whose answer you stopped, for the note under it. */
  stoppedAfter: number | null
  problem: ChatProblem | null
  onRetry: () => void
  onDismissProblem: () => void
  /** Memory she wrote during the last answer, oldest first. */
  receipts: readonly Notification[]
  onUndoReceipt: (receipt: Notification) => void
}

const NEAR_BOTTOM = 96

export function Transcript(props: TranscriptProps) {
  const { messages, state, conversationId, stoppedAfter, problem } = props
  const turns = toTurns(messages)
  const viewport = useRef<HTMLDivElement>(null)
  const lastQuestion = useRef<HTMLDivElement>(null)
  const seen = useRef({ conversation: conversationId, turns: 0 })
  const [atBottom, setAtBottom] = useState(true)
  const [source, setSource] = useState<Citation | null>(null)

  useLayoutEffect(() => {
    const element = viewport.current
    const before = seen.current
    seen.current = { conversation: conversationId, turns: turns.length }
    if (!element || turns.length <= before.turns) {
      if (before.conversation !== conversationId) element?.scrollTo({ top: 0 })
      return
    }
    if (isJustSent(turns[turns.length - 1]) && turns.length === before.turns + 1) {
      const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      lastQuestion.current?.scrollIntoView({ block: 'start', behavior: still ? 'auto' : 'smooth' })
    } else {
      // A conversation that just loaded opens at its latest turn.
      element.scrollTop = element.scrollHeight
    }
  })

  const onScroll = () => {
    const element = viewport.current
    if (!element) return
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < NEAR_BOTTOM)
  }

  const problemNotice = problem && (
    <Notice
      tone="critical"
      live
      title={problem.title}
      action={
        <span className={styles.problemActions}>
          {problem.retry && (
            <Button size="sm" variant="secondary" onClick={props.onRetry}>
              Ask again
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={props.onDismissProblem}>
            Dismiss
          </Button>
        </span>
      }
    >
      {problem.body}
    </Notice>
  )

  return (
    <div className={styles.frame}>
      <div ref={viewport} className={styles.viewport} onScroll={onScroll} aria-busy={state !== 'idle'}>
        <div className={styles.column}>
          {turns.map((turn, index) => {
            const last = index === turns.length - 1
            // Only a turn in progress fills the view: a question you just sent
            // rises to the top with room for the answer below. An opened
            // conversation ends at the bottom with the turns before it in sight.
            const live = last && (isJustSent(turn) || state !== 'idle')
            const stopped = turn.question !== null && stoppedAfter === turn.question.id && !turn.answer?.content
            return (
              <article
                key={turn.key}
                className={cx(styles.turn, live && styles.live)}
                aria-label={turn.question ? undefined : 'Veronica'}
              >
                {turn.question && (
                  <div ref={last ? lastQuestion : undefined} className={styles.questionAnchor}>
                    <Question text={turn.question.content} />
                  </div>
                )}
                {(turn.answer || (last && state !== 'idle')) && (
                  <Answer
                    message={turn.answer}
                    live={last}
                    state={state}
                    explained={stopped || (last && problem !== null)}
                    onCite={setSource}
                  />
                )}
                {stopped && (
                  <p className={styles.stopped}>
                    <Square size={12} aria-hidden="true" />
                    You stopped this answer before it finished.
                  </p>
                )}
                {last && props.receipts.length > 0 && (
                  <div className={styles.receipts}>
                    {props.receipts.map((receipt) => (
                      <MemoryLine key={receipt.id} receipt={receipt} onUndo={() => props.onUndoReceipt(receipt)} />
                    ))}
                  </div>
                )}
                {last && problemNotice}
              </article>
            )
          })}
          {/* No turns at all: a conversation that failed to open, or an empty one. */}
          {turns.length === 0 &&
            (problemNotice || <p className={styles.nothing}>Nothing in this conversation yet. What you write next starts it.</p>)}
        </div>
      </div>
      {!atBottom && (
        <button
          type="button"
          className={styles.jump}
          onClick={() => viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: 'smooth' })}
        >
          <ArrowDown size={15} aria-hidden="true" />
          Jump to latest
        </button>
      )}
      <SourceSheet citation={source} onClose={() => setSource(null)} />
    </div>
  )
}

/** A short question is a heading; a long or many-line one is shown whole, as a quote. */
function Question({ text }: { text: string }) {
  if (text.length <= 160 && !text.includes('\n')) return <h2 className={styles.question}>{text}</h2>
  return (
    <>
      <h2 className="visually-hidden">{text.slice(0, 80)}</h2>
      <p className={styles.longQuestion}>{text}</p>
    </>
  )
}
