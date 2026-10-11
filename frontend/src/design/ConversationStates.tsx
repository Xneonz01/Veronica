// The conversation in every state it can be in, from the real components with
// made-up turns: the empty start, thinking, writing, waiting for your OK,
// settled with sources, stopped, failed, and a page of rich text. What the
// model would produce live is judged here without it.

import { useRef, useState, type ReactNode } from 'react'
import type { ChatMessage, ChatState } from '../chat/types'
import type { Attachment } from '../chat/useAttachments'
import { chatProblem } from '../conversation/chatProblems'
import { Composer } from '../conversation/Composer'
import { Transcript } from '../conversation/Transcript'
import { Welcome } from '../conversation/Welcome'
import styles from './ConversationStates.module.css'
import * as turns from './fixtures'
import { Section, Tile, Tiles } from './Specimen'

type State = {
  label: string
  note: string
  messages: ChatMessage[]
  state?: ChatState
  stoppedAfter?: number
  problem?: string
}

const STATES: State[] = [
  { label: 'Thinking', note: 'Her mark breathes; a line says what she is doing', messages: [turns.weekQuestion], state: 'thinking' },
  { label: 'Writing', note: 'The caret sits where the next words land', messages: [turns.weekQuestion, turns.weekPartial], state: 'streaming' },
  { label: 'Waiting for your OK', note: 'The prompt itself floats above the composer', messages: [turns.writeQuestion, turns.writePending], state: 'awaiting_confirmation' },
  { label: 'Settled', note: 'What she did, the answer, where it came from', messages: [turns.roofQuestion, turns.roofAnswer] },
  { label: 'Stopped', note: 'Nothing is kept of a stopped answer, and it says so', messages: [turns.giftQuestion], stoppedAfter: turns.giftQuestion.id },
  { label: 'Failed', note: 'Her words for what went wrong, and a way on', messages: [turns.giftQuestion], problem: 'llm_unreachable' },
]

const ATTACHMENTS: Attachment[] = [
  { id: 1, conversation_id: 1, kind: 'document', name: 'roof-repair.md', size_bytes: 3480, understood: true },
  { id: 2, conversation_id: 1, kind: 'image', name: 'ceiling-stain.jpg', size_bytes: 1_840_000, understood: false },
]

export function ConversationStates() {
  return (
    <Section
      id="compositions"
      title="Conversation"
      lead="The real components with made-up turns, in every state a conversation can be in."
    >
      <Tiles wide>
        <Tile label="A new conversation" note="Greeting, starters, and the composer where your eyes are" span>
          <div className={styles.start}>
            <Welcome project={null} onStarter={() => undefined} />
            <ComposerSpecimen />
          </div>
        </Tile>
        {STATES.map((item) => (
          <Tile key={item.label} label={item.label} note={item.note}>
            <Frame>
              <Transcript
                messages={item.messages}
                state={item.state ?? 'idle'}
                conversationId={1}
                stoppedAfter={item.stoppedAfter ?? null}
                problem={chatProblem(item.problem ?? null)}
                onRetry={() => undefined}
                onDismissProblem={() => undefined}
                receipts={[]}
                onUndoReceipt={() => undefined}
              />
            </Frame>
          </Tile>
        ))}
        <Tile label="Rich text" note="Tables, lists that keep their numbers, code, quotes, plain maths" span>
          <Frame tall>
            <Transcript
              messages={[turns.richQuestion, turns.richAnswer]}
              state="idle"
              conversationId={2}
              stoppedAfter={null}
              problem={null}
              onRetry={() => undefined}
              onDismissProblem={() => undefined}
              receipts={[]}
              onUndoReceipt={() => undefined}
            />
          </Frame>
        </Tile>
        <Tile label="Composer, answering" note="Send becomes Stop in the same place" span>
          <ComposerSpecimen busy attachments={ATTACHMENTS} uploading={['quotes.pdf']} />
        </Tile>
      </Tiles>
    </Section>
  )
}

function Frame({ children, tall = false }: { children: ReactNode; tall?: boolean }) {
  return <div className={tall ? styles.tall : styles.frame}>{children}</div>
}

function ComposerSpecimen({
  busy = false,
  attachments = [],
  uploading = [],
}: {
  busy?: boolean
  attachments?: Attachment[]
  uploading?: string[]
}) {
  const [value, setValue] = useState('')
  const field = useRef<HTMLTextAreaElement | null>(null)
  return (
    <Composer
      value={value}
      onChange={setValue}
      onSend={() => setValue('')}
      onStop={() => undefined}
      busy={busy}
      lane="open"
      attachments={attachments}
      uploading={uploading}
      onAttach={() => undefined}
      onRemoveAttachment={() => undefined}
      fieldRef={field}
    />
  )
}
