// The companion: a small panel summoned by a global hotkey and dismissed with Esc.
//
// It reuses the chat (`useChat`) and the conversation's own answer rendering, so
// the two windows can't drift apart. It is a panel, not a page: no sidebar and no
// attachments; a field that has the keyboard the moment it appears, the answer,
// and "Open full" to carry the conversation into the main window.

import { ArrowUpRight, Square } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useChat } from '../chat/useChat'
import { QuickSave } from '../components/companion/QuickSave'
import { Answer } from '../conversation/Answer'
import { chatProblem } from '../conversation/chatProblems'
import { toTurns } from '../conversation/turns'
import { expandToMain, hideCompanion, isDesktop, listen } from '../desktop/tauri'
import { MOD } from '../lib/shortcuts'
import { Button } from '../ui/Button'
import { Kbd } from '../ui/Kbd'
import { Mark } from '../ui/Mark'
import styles from './Companion.module.css'

export function Companion() {
  const chat = useChat()
  const [text, setText] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const busy = chat.state !== 'idle'
  const turns = toTurns(chat.messages)
  const problem = chatProblem(chat.errorKind, chat.errorDetail)

  const focusInput = useCallback(() => {
    input.current?.focus()
    input.current?.select()
  }, [])

  useEffect(() => {
    focusInput()
    // In a browser tab there is no desktop shell to summon it.
    if (!isDesktop()) return
    // Rust emits this on every summon: the window is shown and hidden rather than
    // recreated, so mounting alone would only ever fire once.
    const stop = listen('companion://summoned', focusInput)
    return () => {
      void stop.then((unlisten) => unlisten())
    }
  }, [focusInput])

  useEffect(() => {
    const element = scroller.current
    if (element) element.scrollTop = element.scrollHeight
  }, [chat.messages])

  // On the window, not the panel: clicking an answer moves focus to <body>, where a
  // handler on the panel would stop hearing Escape. Escape always dismisses, as the
  // header says; an answer still coming finishes in the background.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      void hideCompanion()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = () => {
    if (busy || !text.trim()) return
    if (chat.send(text)) setText('') // the text stays if it couldn't be sent
  }

  // A message sent back unread goes back in the field once, over nothing typed since.
  const { returned, clearReturned } = chat
  useEffect(() => {
    if (!returned) return
    setText((current) => (current.trim() ? current : returned.text))
    input.current?.focus()
    clearReturned()
  }, [returned, clearReturned])

  return (
    <div className={styles.panel}>
      {/* The whole strip moves the window: it is frameless, so nothing else can. */}
      <header data-tauri-drag-region className={styles.bar}>
        <span className={busy ? styles.markBusy : styles.mark} aria-hidden="true">
          <Mark size={18} />
        </span>
        <span data-tauri-drag-region className={styles.name}>
          Veronica
        </span>
        {chat.lane === 'offline' && <span className={styles.offline}>Reconnecting…</span>}
        {chat.lane === 'unavailable' && <span className={styles.offline}>Chat is paused</span>}
        <span className={styles.barActions}>
          <Button
            size="sm"
            variant="ghost"
            trailingIcon={<ArrowUpRight size={14} aria-hidden="true" />}
            onClick={() => void expandToMain(chat.conversationId)}
          >
            Open full
          </Button>
          <button type="button" className={styles.esc} onClick={() => void hideCompanion()} aria-label="Dismiss">
            <Kbd>Esc</Kbd>
          </button>
        </span>
      </header>

      {/* The answer is announced once, whole; a live region over the stream would
          read out every word as it arrived. */}
      <p className="visually-hidden" role="status">
        {chat.announcement}
      </p>
      <div ref={scroller} className={styles.answers}>
        {turns.length === 0 ? (
          <div className={styles.empty}>
            <p>Ask something, and the answer appears here.</p>
            <p>
              Or write a note and keep it with <Kbd>{`${MOD} Enter`}</Kbd>
            </p>
          </div>
        ) : (
          turns.map((turn, index) => (
            <section key={turn.key} className={styles.turn}>
              {turn.question && <h2 className={styles.question}>{turn.question.content}</h2>}
              {(turn.answer || (index === turns.length - 1 && busy)) && (
                <Answer message={turn.answer} live={index === turns.length - 1} state={chat.state} onCite={() => undefined} />
              )}
            </section>
          ))
        )}
      </div>

      <footer className={styles.foot}>
        <textarea
          ref={input}
          className={styles.field}
          rows={2}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          aria-label="Ask Veronica, or write something to keep"
          // Never disabled: keeping a note goes over plain HTTP, so a dropped chat
          // connection mustn't stop you saving one.
          placeholder="Ask, or jot something down…"
        />
        <div className={styles.actions}>
          <span className={styles.hint}>
            <Kbd>Enter</Kbd> to ask
          </span>
          {/* Keeping is its own action, never guessed from how the sentence reads. */}
          <QuickSave text={text} onSaved={() => setText('')} />
          {busy && (
            <Button size="sm" variant="secondary" icon={<Square size={11} fill="currentColor" aria-hidden="true" />} onClick={chat.stop}>
              Stop
            </Button>
          )}
        </div>
        {problem && (
          <p className={styles.problem} role="alert">
            {problem.title}. {problem.body}
          </p>
        )}
      </footer>
    </div>
  )
}
