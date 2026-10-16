// Her side of a turn: her mark in the gutter, the actions she took, the answer,
// and once it has settled, where it came from and a way to copy it. While she is
// working the mark breathes and a line says what she is doing.

import { Brain, Check, CircleAlert, Copy, FolderOpen, Globe, Plug } from 'lucide-react'
import { useState } from 'react'
import type { ChatMessage, ChatState, Citation } from '../chat/types'
import { fromToolServer } from '../lib/capabilities'
import { IconButton } from '../ui/Button'
import { cx } from '../ui/cx'
import { Mark } from '../ui/Mark'
import styles from './Answer.module.css'
import { RichText } from './RichText'
import { Sources } from './Sources'
import { ToolCard } from './ToolCard'

export type AnswerProps = {
  /** Null until her first word or action arrives. */
  message: ChatMessage | null
  /** This is the turn she is answering now. */
  live: boolean
  state: ChatState
  /** Why there are no words is said beside it (a stop, a failure), so "no answer
      came back" would only repeat it. */
  explained?: boolean
  onCite: (citation: Citation) => void
}

export function Answer({ message, live, state, explained = false, onCite }: AnswerProps) {
  const working = live && state !== 'idle'
  const streaming = working && Boolean(message?.streaming)
  const settled = message !== null && !working && !message.streaming
  const citations = message?.citations ?? []
  const cite = (n: number) => {
    const citation = citations.find((item) => item.n === n)
    if (citation) onCite(citation)
  }

  return (
    <div className={styles.answer}>
      <span className={cx(styles.gutter, working && styles.working)} aria-hidden="true">
        <Mark size={20} />
      </span>
      <div className={styles.body}>
        {message?.tools.map((run, index) => <ToolCard key={`${run.name}-${index}`} run={run} />)}
        {message?.content ? (
          <RichText source={message.content} citations={citations} onCite={cite} streaming={streaming} />
        ) : null}
        {working && !message?.content && <Status state={state} />}
        {working && message?.content && state === 'awaiting_confirmation' && <Status state={state} />}
        {settled && message.caveats?.map((caveat, index) => (
          <p key={`${caveat.family}-${index}`} className={styles.caveat}>
            <CircleAlert size={16} className={styles.caveatIcon} aria-hidden="true" />
            {caveat.text}
          </p>
        ))}
        {settled && message.content && <Footer message={message} />}
        {settled && !message.content.trim() && !explained && (
          <p className={styles.empty}>No answer came back. Ask again if you still need one.</p>
        )}
        {settled && citations.length > 0 && <Sources citations={citations} onOpen={onCite} />}
      </div>
    </div>
  )
}

function Status({ state }: { state: ChatState }) {
  if (state === 'awaiting_confirmation') {
    return (
      <p className={styles.waiting} role="status">
        <span className={styles.waitingDot} aria-hidden="true" />
        Waiting for your OK below
      </p>
    )
  }
  // Her breathing mark already says she's working; the words shimmer instead of
  // adding a second set of dots beside it.
  return (
    <p className={styles.thinking} role="status">
      Thinking it through
    </p>
  )
}

const WEB_TOOLS = new Set(['web_search', 'web_read'])

function Footer({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false)
  const recalled = message.tools.some((run) => run.name === 'memory_recall' && run.ok)
  const browsed = message.tools.some((run) => WEB_TOOLS.has(run.name) && run.ok)
  const served = message.tools.some((run) => fromToolServer(run.name) && run.ok)
  // The web first, then a tool server: of everything an answer can draw on, they are
  // what nobody vouches for.
  const trust = browsed
    ? { icon: <Globe size={14} aria-hidden="true" />, text: 'From the web' }
    : served
      ? { icon: <Plug size={14} aria-hidden="true" />, text: 'From a tool server' }
      : message.citations.length > 0
      ? { icon: <FolderOpen size={14} aria-hidden="true" />, text: 'From your files' }
      : recalled
        ? { icon: <Brain size={14} aria-hidden="true" />, text: 'From what I remember' }
        : null

  const copy = () => {
    void navigator.clipboard?.writeText(message.content).then(
      () => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1600)
      },
      () => setCopied(false),
    )
  }

  return (
    <div className={styles.footer}>
      {trust && (
        <span className={styles.trust}>
          {trust.icon}
          {trust.text}
        </span>
      )}
      <IconButton
        size="sm"
        label={copied ? 'Copied' : 'Copy answer'}
        icon={copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
        onClick={copy}
      />
    </div>
  )
}
